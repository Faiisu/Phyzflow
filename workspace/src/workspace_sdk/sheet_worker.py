"""Start a sheet worker using its Workspace interpreter and Phyzflow's SDK."""

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys


if __name__ == "__main__":
    sdk = spec_from_file_location("workspace_sdk", Path(__file__).with_name("__init__.py"))
    package = sys.modules["workspace_sdk"] = module_from_spec(sdk)
    sdk.loader.exec_module(package)

    from workspace_sdk.workspace_ui import serve_flow

    ready = Path(sys.argv[1])
    pending = ready.with_suffix(".tmp")

    def publish(reply):
        if not ready.parent.is_dir():
            return
        pending.write_text(json.dumps(reply), encoding="utf-8")
        pending.replace(ready)

    serve_flow(Path.cwd(), json.loads(sys.argv[2]), publish, run_token=sys.argv[3] if len(sys.argv) > 3 else None)
