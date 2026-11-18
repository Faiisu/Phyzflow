"""Start a sheet worker using its Workspace interpreter and Phyzflow's SDK."""

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys


if __name__ == "__main__":
    sdk = spec_from_file_location("workspace_sdk", Path(__file__).with_name("__init__.py"))
    package = sys.modules["workspace_sdk"] = module_from_spec(sdk)
    sdk.loader.exec_module(package)

    if len(sys.argv) >= 3 and sys.argv[1] == "--check":
        from contextlib import redirect_stdout
        from workspace_sdk.project import ValidationError, load_project

        target = Path(sys.argv[2])
        pending = target.with_suffix(".tmp")
        try:
            with redirect_stdout(sys.stderr), load_project(Path.cwd(), None) as flow:
                payload = {"valid": True, "steps": [step.id for step in flow.steps]}
        except ValidationError as error:
            payload = {"valid": False, "stage": "validation", "error": str(error)}
        except Exception as error:
            payload = {"valid": False, "stage": "import", "error": f"{type(error).__name__}: {error}"}
        pending.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
        pending.replace(target)
        raise SystemExit(0)

    from workspace_sdk.workspace_ui import serve_flow

    ready = Path(sys.argv[1])
    pending = ready.with_suffix(".tmp")

    def publish(reply):
        if not ready.parent.is_dir():
            return
        pending.write_text(json.dumps(reply), encoding="utf-8")
        pending.replace(ready)

    serve_flow(
        Path.cwd(), json.loads(sys.argv[2]), publish,
        run_token=sys.argv[3] if len(sys.argv) > 3 else None,
        main=len(sys.argv) > 4 and sys.argv[4] == "1",
    )
