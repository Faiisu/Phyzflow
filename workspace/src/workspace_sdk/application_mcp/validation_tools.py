import json
import os
from contextlib import suppress
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen
from tempfile import TemporaryDirectory
from typing import Any

from ..agent_supervisor import spawn_supervised
from ..sheet_locks import SheetLockedError
from ..workspace_environment import workspace_interpreter
from .access import WorkspaceAccess, resolve_workspace_sheet


def register_validation_tools(server, access: WorkspaceAccess) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    from .tool_contract import OptionalEditSession, SheetName, WorkspaceId, read_tool

    @server.tool(**read_tool(
        "Validate flow",
        "Load the sheet's saved flow.py in the Workspace .venv without running any step. Returns valid, the "
        "declared step IDs and loop edges, or the failing stage and error.",
    ))
    async def validate_flow(
        workspace_id: WorkspaceId, sheet: SheetName = "Main", session_id: OptionalEditSession = None,
    ) -> dict[str, Any]:
        from anyio import get_cancelled_exc_class, to_thread

        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        interpreter = workspace_interpreter(root)
        if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
            raise ToolError("Workspace Python environment is unavailable.")
        principal = access.principal()
        if session_id is not None:
            try:
                flow_sheet.edit_lease.verify_write(principal, session_id)
            except (SheetLockedError, ValueError, OSError) as error:
                raise ToolError(str(error)) from None

        with TemporaryDirectory(prefix="phyzflow-check-") as temp_dir:
            result_path = Path(temp_dir) / "result.json"
            worker = Path(__file__).parent.parent / "sheet_worker.py"
            argv = [str(interpreter), "-I", str(worker), "--check", str(result_path), str(root)]
            env = {
                **{key: value for key, value in os.environ.items() if not key.startswith("PHYZFLOW_")},
                "PATH": f"{interpreter.parent}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
                "VIRTUAL_ENV": str(interpreter.parent.parent),
                "PYTHONNOUSERSITE": "1",
                "PYTHONUNBUFFERED": "1",
            }

            def spawn():
                options = {
                    "cwd": flow_sheet.root,
                    "env": env,
                    "stdin": DEVNULL,
                    "stdout": DEVNULL,
                    "stderr": PIPE,
                }
                if os.name == "posix":
                    return spawn_supervised(argv, **options)
                return Popen(argv, **options), None

            lifeline = None
            process = None
            registered = False
            disconnected = False
            try:
                process, lifeline = await to_thread.run_sync(spawn)
                if session_id is not None:
                    try:
                        flow_sheet.edit_lease.register_process(session_id, process, lifeline)
                        registered = True
                    except (SheetLockedError, ValueError, OSError) as error:
                        raise ToolError(str(error)) from None
                _, stderr = await to_thread.run_sync(process.communicate, abandon_on_cancel=True)
            except BaseException as error:
                if isinstance(error, get_cancelled_exc_class()):
                    disconnected = True
                raise
            finally:
                if process is not None:
                    flow_sheet.edit_lease.cleanup_process(
                        session_id if registered else None,
                        process,
                        lifeline,
                    )
                elif lifeline is not None:
                    with suppress(OSError):
                        os.close(lifeline)
                if disconnected and session_id is not None:
                    flow_sheet.edit_lease.mark_disconnected(session_id)
            if session_id is not None and flow_sheet.edit_lease.is_invalidated(session_id):
                raise ToolError("This editing session is no longer active.")
            if result_path.is_file():
                data = json.loads(result_path.read_text(encoding="utf-8"))
                return {"sheet": sheet, **data}
            detail = (stderr or b"").decode("utf-8", errors="replace").strip()
            return {
                "sheet": sheet,
                "valid": False,
                "stage": "import",
                "error": detail[:4096] or "Flow validation did not complete.",
            }
