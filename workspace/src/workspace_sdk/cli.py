import argparse
from contextlib import ExitStack
from importlib.metadata import PackageNotFoundError, distribution
import json
import os
from pathlib import Path
import sqlite3
import sys
import traceback

from .node_settings import SettingsStore
from .project import ValidationError, load_project, workspace_format_notice
from .runner import run
from .sheets import create_sheet, discover_flows, find_sheet, initialize_workspace, workspace_root_for_sheet
from .ui_settings import ApplicationNetwork, load_ui_settings, prompt_ui_settings


def report_event(event):
    if "step_id" not in event:
        return
    if "output_preview" in event:
        print(f"{event['step_id']}: {event['output_preview']}", file=sys.stderr, flush=True)
        return
    print(f"{event['step_id']}: {event['status']}", file=sys.stderr)
    if event["status"] == "failed":
        print(f"{event['file']}:{event['line']}", file=sys.stderr)
        print(event["traceback"], end="", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(prog="workspace")
    parser.add_argument("--project", type=Path, help="Select a Workspace directory (default: current directory).")
    commands = parser.add_subparsers(dest="command", required=True)
    version_parser = commands.add_parser("version", help="Report the installed Phyzflow release and module versions.")
    version_parser.add_argument("--json", action="store_true", help="Print the release manifest as JSON.")
    check_parser = commands.add_parser("check", help="Validate a Python flow.")
    check_parser.add_argument("--sheet", help="Validate a named Workspace sheet.")
    run_parser = commands.add_parser("run", help="Validate and run the current flow.")
    run_parser.add_argument("--sheet", help="Run a named Workspace sheet.")
    run_parser.add_argument("input", help="A JSON object with the flow inputs.")
    commands.add_parser("sheets", help="List sheet names and Python entry points as JSON.")
    init_parser = commands.add_parser("init", help="Initialize a Workspace in a new or empty directory.")
    init_parser.add_argument("directory", type=Path, nargs="?", help="Directory to initialize.")
    init_parser.add_argument("--with-main", action="store_true", help="Include a runnable Main sheet.")
    new_parser = commands.add_parser("new-sheet", help="Create a runnable Python sheet.")
    new_parser.add_argument("name", help="The name of the new sheet.")
    ui_command = commands.add_parser("ui", help="Serve the Workspace UI.")
    ui_command.add_argument("--ipv4", help="Override the IPv4 bind address from setting.json (default: 127.0.0.1).")
    ui_command.add_argument("--port", type=int, help="Override the port from setting.json (default: 18080).")
    ui_command.add_argument("--prompt", action="store_true", help="Ask for the IPv4 bind address and port before starting.")
    ui_command.add_argument("--single-flow", action="store_true", help="Open the current flow without sheets.")
    app_parser = commands.add_parser("app", help="Open the Phyzflow application and Workspace list.")
    app_parser.add_argument("--ipv4", help=f"Override the saved IPv4 bind address (default: {ApplicationNetwork().ipv4}).")
    app_parser.add_argument("--port", type=int, help="Override the saved HTTP port (default: 18080). Use 0 for an available port.")
    app_parser.add_argument("--state-dir", type=Path, default=Path.home() / ".phyzflow", help="Application data directory.")
    deploy_parser = commands.add_parser("deploy", help="Validate and serve a Linux ARM64 build.")
    deploy_parser.add_argument("--sdk", type=Path, required=True, help="Path to the Workspace SDK source.")
    deploy_parser.add_argument("--context", help="Override deploy.context in setting.json.")
    deploy_parser.add_argument("--ipv4", help="Host IPv4 bind address (default: 127.0.0.1).")
    deploy_parser.add_argument("--port", type=int, help="Host port (default: 8080), or 0 for an available port.")
    deploy_parser.add_argument("--container-port", type=int, help="Port used by the container service (default: 8000).")
    args = parser.parse_args()

    if args.command == "version":
        try:
            release = distribution("workspace-sdk").read_text("phyzflow-release.json")
        except PackageNotFoundError:
            release = None
        if release is None:
            print("Phyzflow: This installation has no release manifest.", file=sys.stderr)
            return 1
        manifest = json.loads(release)
        if args.json:
            print(release, end="")
        else:
            print(f"Phyzflow {manifest['version']} ({manifest['commit']})")
            for name, module in manifest["modules"].items():
                print(f"{name}: {module['last_changed_version']} ({module['fingerprint']})")
        return 0

    if args.command == "app":
        from .application import serve_application

        try:
            serve_application(args.state_dir.expanduser().resolve(), args.port, ipv4=args.ipv4)
        except (OSError, ValueError, sqlite3.Error) as error:
            print(f"Phyzflow: {error}", file=sys.stderr)
            return 1
        return 0

    if args.command == "init" and args.directory is not None and args.project is not None:
        parser.error("Use either --project or an init directory.")
    try:
        root = (getattr(args, "directory", None) or args.project or Path.cwd()).expanduser().resolve()
        if args.command == "init":
            initialize_workspace(root, with_main=args.with_main)
            print(root)
            return 0
        if not root.is_dir():
            raise ValueError(f"Workspace directory does not exist: {root}")
        name = getattr(args, "sheet", None)
        format_root = workspace_root_for_sheet(root) if name is None and args.command in ("check", "run") else root
        if notice := workspace_format_notice(format_root):
            print(notice, file=sys.stderr)
        if args.command == "sheets":
            print(json.dumps([{"name": title, "file": str(path / "flow.py")} for title, path in discover_flows(root)]))
            return 0
        if args.command == "new-sheet":
            print(create_sheet(root, args.name))
            return 0
        if name is not None:
            path = find_sheet(root, name)
            if path is None:
                raise ValueError(f"No sheet named {name!r} in {root}. List its sheets with 'workspace sheets'.")
            root = path
        os.chdir(root)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Workspace: {error}", file=sys.stderr)
        return 1
    if args.command == "deploy":
        from .deploy import deploy

        return deploy(
            root, args.sdk.resolve(), args.context, args.port,
            ipv4=args.ipv4, container_port=args.container_port,
        )

    if args.command == "ui":
        try:
            args.ui_settings = load_ui_settings(root, args.port, ipv4_override=args.ipv4)
            if args.prompt:
                args.ui_settings = prompt_ui_settings(args.ui_settings)
        except KeyboardInterrupt:
            print(file=sys.stderr)
            return 130
        except (OSError, ValueError) as error:
            print(f"UI settings: {error}", file=sys.stderr)
            return 1

    if args.command == "ui" and not args.single_flow:
        from .workspace_ui import serve_workspace

        try:
            serve_workspace(root, discover_flows(root), args.ui_settings)
        except (OSError, ValueError) as error:
            print(f"Web UI: {error}", file=sys.stderr)
            return 1
        return 0

    loaded_sources: dict[Path, str] | None = {} if args.command == "ui" else None
    with ExitStack() as project:
        try:
            flow = project.enter_context(load_project(root, loaded_sources))
        except ValidationError as error:
            print(f"Validation: {error}", file=sys.stderr)
            return 1
        except Exception as error:
            print("Flow import failed.", file=sys.stderr)
            traceback.print_exception(error)
            return 1
        return run_command(args, flow, root, loaded_sources)


def run_command(args, flow, root, loaded_sources):
    if args.command == "ui":
        from .web import serve

        try:
            serve(flow, root, args.ui_settings.port, loaded_sources, zoom=args.ui_settings.zoom,
                  ipv4=args.ui_settings.ipv4)
        except (OSError, ValueError) as error:
            print(f"Web UI: {error}", file=sys.stderr)
            return 1
        return 0

    if args.command == "check":
        print("Project checks passed.")
        return 0

    try:
        inputs = json.loads(args.input)
        if not isinstance(inputs, dict):
            raise ValueError("Run input must be a JSON object.")
        result = run(flow, inputs, report_event, settings=SettingsStore(root).snapshot(flow.steps))
        if result.status == "failed":
            return 1
        print(json.dumps(result.output, allow_nan=False))
    except (ValueError, TypeError) as error:
        print(f"Run: {error}", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error):
        print("Run: Could not read node settings.", file=sys.stderr)
        return 1
    return 0
