import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import traceback

from .project import ValidationError, load_project
from .runner import run


def report_event(event):
    if "step_id" not in event:
        return
    print(f"{event['step_id']}: {event['status']}", file=sys.stderr)
    if event["status"] == "failed":
        print(f"{event['file']}:{event['line']}", file=sys.stderr)
        print(event["traceback"], end="", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(prog="workspace")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Validate the current project.")
    run_parser = commands.add_parser("run", help="Validate and run the current flow.")
    run_parser.add_argument("input", help="A JSON object with the flow inputs.")
    ui_command = commands.add_parser("ui", help="Serve the flow UI on localhost.")
    ui_command.add_argument("--port", type=int, default=8765)
    deploy_parser = commands.add_parser("deploy", help="Validate and serve a Linux ARM64 build.")
    deploy_parser.add_argument("--sdk", type=Path, required=True, help="Path to the Workspace SDK source.")
    deploy_parser.add_argument("--context", help="Local Docker context.")
    deploy_parser.add_argument("--port", type=int, default=8080, help="Loopback port, or 0 for an available port.")
    args = parser.parse_args()

    root = Path.cwd().resolve()
    if args.command == "deploy":
        from .deploy import deploy

        return deploy(root, args.sdk.resolve(), args.context, args.port)

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
            serve(flow, root, args.port, loaded_sources)
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
        result = run(flow, inputs, report_event)
        if result.status == "failed":
            return 1
        print(json.dumps(result.output, allow_nan=False))
    except (ValueError, TypeError) as error:
        print(f"Run: {error}", file=sys.stderr)
        return 1
    return 0
