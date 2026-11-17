import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sqlite3
import sys
import traceback

from .node_settings import SettingsStore
from .project import ValidationError, load_project
from .runner import run
from .sheets import create_sheet, discover_flows, initialize_workspace
from .ui_settings import load_ui_settings


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
    check_parser = commands.add_parser("check", help="Validate a Python flow.")
    check_parser.add_argument("--sheet", help="Validate a named Workspace sheet.")
    run_parser = commands.add_parser("run", help="Validate and run the current flow.")
    run_parser.add_argument("--sheet", help="Run a named Workspace sheet.")
    run_parser.add_argument("input", help="A JSON object with the flow inputs.")
    commands.add_parser("sheets", help="List sheet names and Python entry points as JSON.")
    init_parser = commands.add_parser("init", help="Initialize a Workspace in a new or empty directory.")
    init_parser.add_argument("directory", type=Path, nargs="?", help="Directory to initialize.")
    new_parser = commands.add_parser("new-sheet", help="Create a runnable Python sheet.")
    new_parser.add_argument("name", help="The name of the new sheet.")
    ui_command = commands.add_parser("ui", help="Serve the Workspace UI on localhost.")
    ui_command.add_argument("--port", type=int, help="Override the port from setting.json (default: 18080).")
    ui_command.add_argument("--single-flow", action="store_true", help="Open the current flow without sheets.")
    deploy_parser = commands.add_parser("deploy", help="Validate and serve a Linux ARM64 build.")
    deploy_parser.add_argument("--sdk", type=Path, required=True, help="Path to the Workspace SDK source.")
    deploy_parser.add_argument("--context", help="Local Docker context.")
    deploy_parser.add_argument("--port", type=int, default=8080, help="Loopback port, or 0 for an available port.")
    args = parser.parse_args()

    if args.command == "init" and args.directory is not None and args.project is not None:
        parser.error("Use either --project or an init directory.")
    try:
        root = (getattr(args, "directory", None) or args.project or Path.cwd()).expanduser().resolve()
        if args.command == "init":
            initialize_workspace(root)
            print(root)
            return 0
        if not root.is_dir():
            raise ValueError(f"Workspace directory does not exist: {root}")
        if args.command == "sheets":
            print(json.dumps([{"name": title, "file": str(path / "flow.py")} for title, path in discover_flows(root)]))
            return 0
        if args.command == "new-sheet":
            print(create_sheet(root, args.name))
            return 0
        if (name := getattr(args, "sheet", None)) is not None:
            path = next((path for title, path in discover_flows(root) if title == name), None)
            if path is None:
                raise ValueError(f"No sheet named {name!r} in {root}. List its sheets with 'workspace sheets'.")
            root = path
        os.chdir(root)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Workspace: {error}", file=sys.stderr)
        return 1
    if args.command == "deploy":
        from .deploy import deploy

        return deploy(root, args.sdk.resolve(), args.context, args.port)

    if args.command == "ui":
        try:
            args.ui_settings = load_ui_settings(root, args.port)
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
            serve(flow, root, args.ui_settings.port, loaded_sources, zoom=args.ui_settings.zoom)
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
