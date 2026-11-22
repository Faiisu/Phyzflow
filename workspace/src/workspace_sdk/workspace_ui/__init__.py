from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path

from ..sheet_runs import RunStateStore
from ..ui_settings import UISettings
from ..web import UIRequestHandler, ui_address
from .flow_sheet import FlowSheet as FlowSheet, ReloadConflict as ReloadConflict, ReloadDeferred as ReloadDeferred
from .processes import (
    OpenFlow as OpenFlow,
    WorkspaceProcessRunningError as WorkspaceProcessRunningError,
    active_workspace_pids as active_workspace_pids,
    serve_flow as serve_flow,
    terminate as terminate,
    verify_workspace_processes_stopped as verify_workspace_processes_stopped,
    workspace_command_pattern as workspace_command_pattern,
)
from .routes import VIEW_PATH as VIEW_PATH
from .session import workspace_routes as workspace_routes


def serve_workspace(
    root: Path,
    flows: list[tuple[str, Path]],
    settings: UISettings,
) -> None:
    state_dir = root / ".workspace"
    if state_dir.is_symlink():
        raise ValueError("Workspace state directory must not be a symbolic link.")
    with closing(RunStateStore(state_dir)) as dashboard_state:
        dispatch, close, _ = workspace_routes(root, flows, settings, dashboard_state=dashboard_state)

        class Handler(UIRequestHandler):
            def do_GET(self) -> None:
                dispatch(self, self.path)

            do_POST = do_GET

        try:
            with ThreadingHTTPServer((settings.ipv4, settings.port), Handler) as server:
                print(f"Workspace UI: {ui_address(settings.ipv4, server.server_port)}", flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
        finally:
            close()
