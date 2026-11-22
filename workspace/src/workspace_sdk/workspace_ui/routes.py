from __future__ import annotations

from contextlib import nullcontext
from http import HTTPStatus
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import unquote

from ..dashboard import (
    DASHBOARD_BRIDGE_VERSION,
    dashboard_assets,
)
from ..dashboard_editor import SOURCE_ROUTE as DASHBOARD_SOURCE_ROUTE, send_dashboard_edit
from ..execution import FlowBusy, sheet_lock
from ..node_settings import parse_setting_defaults
from ..node_sources import edit_source_file, node_files, node_settings_path, node_source_path
from ..project import ValidationError, workspace_format_notice
from ..sheet_locks import SheetLockedError
from ..sheets import (
    can_delete_sheet,
    edit_sheet_flow_source,
    editable_sheet_python_files,
    find_sheet_by_path,
)
from ..web import (
    FLOW_VIEW_ASSETS,
    FLOW_VIEW_ASSET_PATTERN,
    FLOW_VIEW_SUBPATH_PATTERN,
    NODE_FILES_ROUTE,
    NODE_SOURCE_ROUTE,
    STATIC,
    UIRequestHandler,
    is_flow_view_route,
    recovery_graph_revision,
)
from .dashboard_data import placement_snapshot
from .files import send_workspace_files
from .flow_sheet import FlowSheet, ReloadConflict, ReloadDeferred


VIEW_PATH = re.compile(r"/flows/([0-9]{1,8})(?:/revision/([0-9]{1,8}))?/view" + FLOW_VIEW_SUBPATH_PATTERN)
VISUALIZE_LAYOUT_PATH = re.compile(r"/flows/([0-9]{1,8})(?:/revision/[0-9]{1,8})?/view/visualize-layout")
RECOVERY_PATH = re.compile(rf"/flows/([0-9]{{1,8}})/recovery({FLOW_VIEW_ASSET_PATTERN}|/[^/]*)")
RECOVERY_NODE_FILES = re.compile(rf"/flows/([0-9]{{1,8}})/recovery/{NODE_FILES_ROUTE}")
RECOVERY_NODE_SOURCE = re.compile(rf"/flows/([0-9]{{1,8}})/recovery/{NODE_SOURCE_ROUTE}")
FLOW_SOURCE_PATH = re.compile(r"/flows/([0-9]{1,8})/source(?:\?(files|path=([^&]+)))?")
EDIT_LOCK_PATH = re.compile(r"/flows/([0-9]{1,8})/edit-lock")
WORKSPACE_VISUALIZE_LAYOUT = "/dashboard/visualize-layout"
WORKSPACE_PLACEMENTS = "/dashboard/placements"
PLACEMENT_ASSET_PATH = re.compile(r"/dashboard/placement-assets/([0-9a-f]{32})(/data)?")
WORKSPACE_ASSETS = {
    "/": ("workspace.html", "text/html"),
    "/workspace.css": ("workspace.css", "text/css"),
    "/workspace.js": ("workspace.js", "text/javascript"),
    "/live-sync.js": ("live-sync.js", "text/javascript"),
    "/workspace/source-editing.js": ("workspace/source-editing.js", "text/javascript"),
    "/workspace/sheet-dialogs.js": ("workspace/sheet-dialogs.js", "text/javascript"),
    "/workspace/dashboard.js": ("workspace/dashboard.js", "text/javascript"),
    "/workspace/files.js": ("workspace/files.js", "text/javascript"),
    "/dashboard/": FLOW_VIEW_ASSETS["/"],
    "/dashboard/app.css": FLOW_VIEW_ASSETS["/app.css"],
    "/dashboard/app.js": ("workspace/dashboard-view.js", "text/javascript"),
    "/dashboard/canvas-layout.js": FLOW_VIEW_ASSETS["/canvas-layout.js"],
    "/dashboard/dashboard-ui.js": FLOW_VIEW_ASSETS["/dashboard-ui.js"],
    "/dashboard/dashboard-bridge.js": FLOW_VIEW_ASSETS["/dashboard-bridge.js"],
    "/dashboard/live-sync.js": FLOW_VIEW_ASSETS["/live-sync.js"],
    "/dashboard/dashboard-editor.js": FLOW_VIEW_ASSETS["/dashboard-editor.js"],
    "/dashboard/prism-syntax.js": FLOW_VIEW_ASSETS["/prism-syntax.js"],
}


def validate_visualize_layout(panels: object) -> dict:
    if not isinstance(panels, dict) or not panels:
        raise ValueError
    minimums = {"left": 20, "top": 20, "width": 280, "height": 220}
    for key, bounds in panels.items():
        key.encode("utf-8")
        identity = json.loads(key)
        if (
            not isinstance(identity, list) or len(identity) != 2
            or (identity[0] is not None and (not isinstance(identity[0], str) or not identity[0]))
            or not isinstance(identity[1], str) or not identity[1]
            or not isinstance(bounds, dict) or bounds.keys() != minimums.keys()
            or any(
                type(bounds[name]) is not int or bounds[name] < minimum
                or bounds[name] > 10_000_000 or bounds[name] % 20
                for name, minimum in minimums.items()
            )
        ):
            raise ValueError
    return panels


def workspace_dispatch(
    root, sheets, sheets_lock, *, interpreter, is_closing, discovered_sheets,
    create_workspace_sheet, rename_workspace_sheet, delete_workspace_sheet, run_all_sheets, workspace_id, run_state,
    dashboard_state, inspect_workspace_dashboard, add_workspace_dashboard_placement, remove_workspace_dashboard_placement,
):
    dashboard_id = workspace_id or "."

    def dashboard_sheets() -> dict[Path, tuple[int, FlowSheet]]:
        try:
            current_list = discovered_sheets()
        except ValidationError:
            with sheets_lock:
                current_list = list(sheets)
        return {sheet.root: (index, sheet) for index, sheet in enumerate(current_list)
                if sheet is not None and not sheet.closing}

    def panel_scope_available(sheet: FlowSheet, scope: str | None) -> bool:
        if scope is None:
            return True
        step_ids = sheet.dashboard_step_ids()
        return step_ids is None or scope in step_ids

    class Routes:
        def __init__(self, request: UIRequestHandler, path: str):
            self.request = request
            self.path = path

        def workspace_placements(self) -> None:
            if dashboard_state is None or is_closing():
                self.request.send_json({"error": "This Workspace is unavailable."}, HTTPStatus.NOT_FOUND)
                return
            try:
                if self.request.command == "POST":
                    if self.request.headers.get_content_type() != "application/json":
                        raise ValueError
                    data = self.request.read_json_dict(16 * 1024)
                    action = data.get("action")
                    if action == "add":
                        payload = add_workspace_dashboard_placement(
                            data.get("sheet"), data.get("scope"), data.get("id"),
                        )
                    elif action == "remove":
                        payload = remove_workspace_dashboard_placement(data.get("id"), allow_missing=True)
                    else:
                        raise ValueError
                else:
                    payload = inspect_workspace_dashboard()
            except FileNotFoundError:
                self.request.send_json({"error": "Source panel unavailable in this Workspace."}, HTTPStatus.NOT_FOUND)
            except (ValueError, UnicodeError, RecursionError):
                self.request.send_json({"error": "Send a valid dashboard placement."}, HTTPStatus.BAD_REQUEST)
            except (OSError, sqlite3.Error, RuntimeError):
                self.request.send_json({"error": "Could not access dashboard placements."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self.request.send_json(payload)

        def workspace_placement(self, placement_id: str, data: bool) -> None:
            try:
                placement = (
                    dashboard_state.find_workspace_placement(dashboard_id, placement_id)
                    if dashboard_state is not None and not is_closing()
                    else None
                )
                if placement is None:
                    raise FileNotFoundError
                source = find_sheet_by_path(root, placement["sheet"])
                entry = dashboard_sheets().get(source)
                if entry is None or not panel_scope_available(entry[1], placement["scope"]):
                    raise FileNotFoundError
                assets = dashboard_assets(source, placement["scope"], placement["panel_id"], manifest_only=data)
                if data:
                    result = placement_snapshot(entry[1], placement)
                else:
                    result = {"bridge_version": DASHBOARD_BRIDGE_VERSION, "scope": "workspace", **assets}
            except (FileNotFoundError, PermissionError):
                self.request.send_json({"error": "Source panel unavailable."}, HTTPStatus.NOT_FOUND)
            except ValueError as error:
                message = "Could not load dashboard data." if data else str(error)
                self.request.send_json({"error": message}, HTTPStatus.BAD_REQUEST)
            except (OSError, sqlite3.Error, RuntimeError, KeyError, TypeError):
                self.request.send_json({"error": "Could not load the source panel."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                if data:
                    self.request.send_conditional_json(result)
                else:
                    self.request.send_json(result)

        def create_sheet(self) -> None:
            try:
                if self.request.headers.get_content_type() != "application/json":
                    raise ValueError
                data = self.request.read_json_dict(4096)
            except (ValueError, UnicodeError):
                self.request.send_json({"error": "Send a JSON object with a sheet name."}, HTTPStatus.BAD_REQUEST)
                return
            value = data.get("name")

            try:
                created = create_workspace_sheet(value)
            except RuntimeError as error:
                self.request.send_json({"error": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            except FileExistsError:
                self.request.send_json({"error": "That sheet name is already in use. Choose another name."}, HTTPStatus.CONFLICT)
                return
            except ValueError as error:
                self.request.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            except OSError:
                self.request.send_json({"error": "Could not write the flow file. Check the workspace folder permissions."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self.request.send_json({"index": created["index"]}, HTTPStatus.CREATED)

        def delete_sheet(self, index: int) -> None:
            session_id = self.request.headers.get("X-Phyzflow-Edit-Session")
            with sheets_lock:
                sheet = sheets[index]
                if is_closing() or sheet is None:
                    self.request.send_json({"error": "This sheet is no longer available."}, HTTPStatus.CONFLICT)
                    return
                sheet_root = sheet.root
            try:
                result = delete_workspace_sheet(
                    sheet_root,
                    principal="web" if session_id else None,
                    session_id=session_id,
                )
            except (FileNotFoundError, RuntimeError):
                self.request.send_json({"error": "This sheet is no longer available."}, HTTPStatus.CONFLICT)
                return
            except SheetLockedError as error:
                self.request.send_json({"error": str(error), "edit_lock": error.snapshot}, HTTPStatus.CONFLICT)
                return
            except ValueError as error:
                self.request.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            except OSError:
                self.request.send_json({"error": "Could not move the flow folder to deleted flows."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self.request.send_json(result)

        def rename_sheet(self, sheet: FlowSheet) -> None:
            try:
                if self.request.headers.get_content_type() != "application/json":
                    raise ValueError
                data = self.request.read_json_dict(4096)
            except (ValueError, UnicodeError):
                self.request.send_json({"error": "Send a JSON object with a sheet name."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                result = rename_workspace_sheet(
                    sheet, data.get("name"), principal="web",
                    session_id=self.request.headers.get("X-Phyzflow-Edit-Session"),
                )
            except SheetLockedError as error:
                self.request.send_json({"error": str(error), "edit_lock": error.snapshot}, HTTPStatus.CONFLICT)
            except (FileExistsError, FileNotFoundError) as error:
                self.request.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            except ValueError as error:
                self.request.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except OSError:
                self.request.send_json({"error": "Could not save the sheet name."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self.request.send_json(result)

        def view_route(self) -> tuple[FlowSheet, int, str] | None:
            route = VIEW_PATH.fullmatch(self.path)
            if route is None or (index := int(route[1])) >= len(sheets):
                return None
            sheet = sheets[index]
            return (sheet, int(route[2] or "0"), route[3]) if sheet is not None else None

        def sheet_dashboard_source(self, sheet: FlowSheet, revision: int, scope: str) -> None:
            if self.request.headers.get_content_type() != "application/json":
                self.request.send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                return
            try:
                data = self.request.read_json_dict()
            except (ValueError, UnicodeError):
                self.request.send_json({"error": "Send a JSON object up to 1 MiB."}, HTTPStatus.BAD_REQUEST)
                return
            with sheet.lock:
                if sheet.closing or sheet.workspace_closing():
                    self.request.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Workspace is shutting down.")
                    return
                if revision != sheet.revision:
                    self.request.send_error(HTTPStatus.CONFLICT, "The flow has been reloaded. Refresh its sheet.")
                    return
                if not sheet.worker_ready() or sheet.recovery_graph is None:
                    self.request.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Open the flow first.")
                    return
                try:
                    step_id = None if scope == "workflow" else sheet.recovery_graph["steps"][int(scope)]["id"]
                except IndexError:
                    self.request.send_json({"error": "Dashboard scope not found."}, HTTPStatus.NOT_FOUND)
                    return
                send_dashboard_edit(self.request, sheet.root, step_id, data, sheet_lease=sheet.edit_lease)

        def visualize_layout(self, index: int | None = None) -> None:
            sheet = sheets[index] if index is not None and index < len(sheets) else None
            sheet_key = sheet and sheet.sheet_key
            unavailable = "This Workspace is unavailable." if index is None else "This sheet is unavailable."
            state = dashboard_state if index is None else run_state
            identity = dashboard_id if index is None else workspace_id
            if state is None or identity is None or (index is not None and sheet_key is None):
                self.request.send_json({"error": unavailable}, HTTPStatus.NOT_FOUND)
                return
            panels = None
            if self.request.command == "POST":
                try:
                    if self.request.headers.get_content_type() != "application/json":
                        raise ValueError
                    panels = validate_visualize_layout(self.request.read_json_dict().get("panels"))
                except (ValueError, UnicodeError, RecursionError):
                    self.request.send_json({"error": "Send valid dashboard panel bounds."}, HTTPStatus.BAD_REQUEST)
                    return
            try:
                with sheet.lock if sheet is not None else sheets_lock:
                    if is_closing() or (sheet is not None and (sheet.closing or sheet.workspace_closing())):
                        self.request.send_json({"error": unavailable}, HTTPStatus.NOT_FOUND)
                        return
                    if panels is None:
                        result = {"panels": state.load_workspace_placement_layout(identity) if index is None
                                  else state.load_visualize_layout(identity, sheet_key)}
                    else:
                        if index is None:
                            state.save_workspace_placement_layout(identity, panels)
                        else:
                            state.save_visualize_layout(identity, sheet_key, panels)
                        result = {}
            except sqlite3.Error:
                self.request.send_json({"error": "Could not access the saved dashboard layout."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self.request.send_json(result)

        def flow_source(self, index: int, query: str | None, path: str | None) -> None:
            source = revision = None
            relative = unquote(path) if path is not None else "flow.py"
            if self.request.command == "POST":
                try:
                    if query == "files" or self.request.headers.get_content_type() != "application/json":
                        raise ValueError
                    data = self.request.read_json_dict()
                    source, revision = data.get("source"), data.get("revision")
                    if not isinstance(source, str) or not isinstance(revision, str):
                        raise ValueError
                except (ValueError, UnicodeError):
                    self.request.send_json({"error": "Send the source and revision as a JSON object up to 1 MiB."}, HTTPStatus.BAD_REQUEST)
                    return

            sheet = sheets[index] if index < len(sheets) else None
            if source is not None and interpreter is None and sheet is not None and sheet.status() == "running":
                message = f"Cannot edit {'flow.py' if relative == 'flow.py' else 'source'} while the flow is running."
                self.request.send_json({"error": message}, HTTPStatus.CONFLICT)
                return

            def perform_edit() -> dict:
                if sheet is None:
                    raise FileNotFoundError
                with sheet.lock:
                    if sheet.closing or sheet.workspace_closing():
                        raise FileNotFoundError
                    if query == "files":
                        files = editable_sheet_python_files(sheet.root, main=sheet.main)
                        return {"files": sorted(path.relative_to(sheet.root).as_posix() for path in files)}

                    def edit() -> dict[str, str]:
                        return edit_sheet_flow_source(sheet.root, source, revision, relative, main=sheet.main)

                    if source is None:
                        return edit()
                    session_id = self.request.headers.get("X-Phyzflow-Edit-Session")
                    return sheet.edit_lease.write_locked("web", session_id, edit, path=relative)

            filename = "flow.py" if relative == "flow.py" else "source file"
            self.request.send_source_edit(perform_edit, not_found_error=f"This sheet's {filename} is unavailable.")

        def recovery_node(self, index: int, node: int, *, edit: bool = False) -> None:
            sheet = sheets[index] if index < len(sheets) else None
            if sheet is None:
                self.request.send_error(HTTPStatus.NOT_FOUND)
                return
            if edit:
                if self.request.headers.get_content_type() != "application/json":
                    self.request.send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                    return
                try:
                    data = self.request.read_json_dict()
                    path, source, revision = data.get("path"), data.get("source"), data.get("revision")
                    if not isinstance(path, str) or (
                        "source" in data and (not isinstance(source, str) or not isinstance(revision, str))
                    ):
                        raise ValueError
                except (ValueError, UnicodeError):
                    self.request.send_json(
                        {"error": "Send a file path and, to save, the source and revision."}, HTTPStatus.BAD_REQUEST,
                    )
                    return
            with sheet.lock:
                if sheet.closing or sheet.workspace_closing():
                    self.request.send_error(HTTPStatus.NOT_FOUND)
                    return
                graph = sheet.recovery_graph or sheet.source_graph()
                revision_header = self.request.headers.get("X-Phyzflow-Graph-Revision")
                if revision_header is not None and revision_header != recovery_graph_revision(graph["steps"]):
                    self.request.send_json(
                        {"error": "The recovery graph changed. Reload the flow and reopen its node files."},
                        HTTPStatus.CONFLICT,
                    )
                    return
                if node >= len(graph["steps"]):
                    self.request.send_error(HTTPStatus.NOT_FOUND)
                    return
                filename = graph["steps"][node]["source"]
                if not edit:
                    try:
                        data = node_files(sheet.root, filename, include_missing_settings=False)
                    except (OSError, RuntimeError):
                        self.request.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not read the node folder.")
                    else:
                        self.request.send_json(data)
                    return
                def perform_edit() -> dict[str, str]:
                    target = node_source_path(sheet.root, filename, path)
                    if source is None:
                        return edit_source_file(target, None, None)
                    if sheet.status() in ("running", "stopping"):
                        raise FlowBusy("Cannot edit node files while the flow is running.")
                    if sheet.worker_ready():
                        raise FlowBusy("The flow is open. Reopen the node files in the live sheet.")

                    def save() -> dict[str, str]:
                        if target == node_settings_path(sheet.root, filename):
                            parse_setting_defaults(source)
                        return edit_source_file(target, source, revision)

                    with sheet_lock(sheet.root):
                        return sheet.edit_lease.write_locked(
                            "web", self.request.headers.get("X-Phyzflow-Edit-Session"), save, path=path,
                        )

                self.request.send_source_edit(perform_edit, not_found_error="This source file is unavailable in the node folder.")

        def edit_lock(self, index: int) -> None:
            sheet = sheets[index] if index < len(sheets) else None
            if is_closing() or sheet is None or sheet.closing:
                self.request.send_json({"error": "This sheet is unavailable."}, HTTPStatus.NOT_FOUND)
                return
            try:
                if self.request.headers.get_content_type() != "application/json":
                    raise ValueError
                data = self.request.read_json_dict(8192)
                action = data.get("action")
                if action not in ("acquire", "heartbeat", "release", "force_open"):
                    raise ValueError("Choose acquire, heartbeat, release, or force_open.")
                session_id = data.get("session_id")
                if session_id is not None and not isinstance(session_id, str):
                    raise ValueError("session_id must be a string.")
                if session_id is None:
                    session_id = self.request.headers.get("X-Phyzflow-Edit-Session")
            except (ValueError, UnicodeError) as error:
                self.request.send_json({"error": str(error) or "Send a valid JSON edit-lock action."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                if action == "acquire":
                    result = sheet.edit_lease.acquire("web", "web", "Web editor", session_id=session_id)
                elif action == "heartbeat":
                    result = sheet.edit_lease.heartbeat("web", session_id or "")
                elif action == "release":
                    result = sheet.edit_lease.release("web", session_id or "")
                else:
                    force_info = sheet.edit_lease.force_open()
                    reload_error = None
                    if force_info["forced"] and sheet.worker_ready() and sheet.status() not in ("running", "stopping"):
                        try:
                            sheet.open(reload=True)
                        except ReloadConflict:
                            pass
                        except (OSError, RuntimeError, EOFError) as error:
                            if interpreter is None or sheet.worker_ready():
                                reload_error = str(error) or "The flow process exited."
                    if data.get("acquire"):
                        edit_lock = sheet.edit_lease.acquire("web", "web", "Web editor", session_id=session_id)
                    else:
                        edit_lock = sheet.edit_lease.status(session_id)
                    result = {
                        "forced": force_info["forced"],
                        "previous_owner": force_info["previous_owner"],
                        "revision": sheet.revision,
                        "status": sheet.status(),
                        "edit_lock": edit_lock,
                    }
                    if reload_error is not None:
                        result["reload_error"] = reload_error
                    result["worker_ready"] = sheet.worker_ready()
            except SheetLockedError as error:
                self.request.send_json({"error": str(error), "edit_lock": error.snapshot}, HTTPStatus.CONFLICT)
            except (ValueError, OSError) as error:
                self.request.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            else:
                self.request.send_json(result)

        def run_sheet(self, sheet: FlowSheet, revision: int) -> None:
            if self.request.headers.get_content_type() != "application/json":
                self.request.send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                return
            try:
                inputs = self.request.read_json_dict()
            except (ValueError, UnicodeError):
                self.request.send_error(HTTPStatus.BAD_REQUEST, "Send a JSON object up to 1 MiB.")
                return
            try:
                attempt = sheet.start_run(inputs, revision=revision)
            except (FlowBusy, ReloadConflict) as error:
                self.request.send_error(HTTPStatus.CONFLICT, str(error))
                return
            except (OSError, RuntimeError, ValueError) as error:
                self.request.send_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            try:
                self.request.send_response(HTTPStatus.OK)
                self.request.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.request.send_header("Cache-Control", "no-store")
                self.request.end_headers()
                for line in attempt.messages():
                    self.request.wfile.write(line)
            except OSError:
                pass

        def run_all(self) -> None:
            try:
                result = run_all_sheets(by_index=True)
            except ValidationError as error:
                self.request.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            self.request.send_json(result, HTTPStatus.ACCEPTED)

        def forward_view(self, sheet: FlowSheet, revision: int, path: str) -> None:
            headers = {}
            for header_name in ("X-Phyzflow-Edit-Session", "If-None-Match", "Accept-Encoding"):
                header_value = self.request.headers.get(header_name)
                if header_value is not None:
                    headers[header_name] = header_value
            body = None
            if self.request.command == "POST":
                try:
                    size = int(self.request.headers.get("Content-Length", "0"))
                    if not 0 < size <= 1024 * 1024:
                        raise ValueError
                except ValueError:
                    self.request.send_error(HTTPStatus.BAD_REQUEST, "Send a JSON object up to 1 MiB.")
                    return
                body = self.request.rfile.read(size)
                headers["Content-Type"] = self.request.headers.get("Content-Type", "")

            upstream = None
            try:
                try:
                    with sheet.lock if self.request.command == "POST" else nullcontext():
                        if sheet.closing or sheet.workspace_closing():
                            self.request.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Workspace is shutting down.")
                            return
                        worker = sheet.worker
                        if revision != sheet.revision:
                            self.request.send_error(HTTPStatus.CONFLICT, "The flow has been reloaded. Refresh its sheet.")
                            return
                        if worker is None or not worker.url or worker.process.poll() is not None:
                            self.request.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Open the flow first.")
                            return
                        upstream = HTTPConnection(worker.url.removeprefix("http://"), timeout=2)
                        upstream.connect()
                        connection = upstream.sock
                        upstream.request(self.request.command, path, body=body, headers=headers)
                        response = upstream.getresponse()
                        if connection is not None:
                            connection.settimeout(None)
                except (OSError, HTTPException):
                    self.request.send_error(HTTPStatus.BAD_GATEWAY, "The flow process is unavailable.")
                    return

                with response:
                    try:
                        self.request.send_response(response.status)
                        for name in ("Content-Type", "Content-Length", "Cache-Control", "ETag", "Content-Encoding", "Vary"):
                            value = response.getheader(name)
                            if value is not None:
                                self.request.send_header(name, value)
                        self.request.end_headers()
                        if response.status != HTTPStatus.NOT_MODIFIED:
                            while chunk := response.read1(65536):
                                self.request.wfile.write(chunk)
                    except (OSError, HTTPException):
                        return
            finally:
                if upstream is not None:
                    upstream.close()

        def do_GET(self) -> None:
            if not self.request.local_request():
                self.request.send_error(HTTPStatus.FORBIDDEN)
                return
            route, _, query = self.path.partition("?")
            if route == "/files":
                send_workspace_files(self.request, root, route, query, sheets_lock, is_closing)
                return
            if self.path == WORKSPACE_VISUALIZE_LAYOUT:
                self.visualize_layout()
                return
            if self.path == WORKSPACE_PLACEMENTS:
                self.workspace_placements()
                return
            if asset := PLACEMENT_ASSET_PATH.fullmatch(self.path):
                self.workspace_placement(asset[1], asset[2] is not None)
                return
            if self.path == "/dashboard/panel-catalog":
                self.request.send_json({"panels": inspect_workspace_dashboard()["catalog"]})
                return
            if layout := VISUALIZE_LAYOUT_PATH.fullmatch(self.path):
                self.visualize_layout(int(layout[1]))
                return
            if node := RECOVERY_NODE_FILES.fullmatch(self.path):
                self.recovery_node(int(node[1]), int(node[2]))
                return
            if recovery := RECOVERY_PATH.fullmatch(self.path):
                index = int(recovery[1])
                sheet = sheets[index] if index < len(sheets) else None
                graph = sheet.recovery_graph if sheet is not None else None
                if sheet is None:
                    self.request.send_error(HTTPStatus.NOT_FOUND)
                elif recovery[2] == "/project":
                    graph = graph if graph is not None else sheet.source_graph()
                    self.request.send_json({**graph, "graph_revision": recovery_graph_revision(graph["steps"])})
                elif asset := FLOW_VIEW_ASSETS.get(recovery[2]):
                    name, content_type = asset
                    self.request.send_static_asset(STATIC / name, content_type)
                else:
                    self.request.send_error(HTTPStatus.NOT_FOUND)
                return
            if source := FLOW_SOURCE_PATH.fullmatch(self.path):
                self.flow_source(int(source[1]), source[2], source[3])
                return
            if self.path == "/workspace":
                viewer_sessions = {
                    part.strip()
                    for part in (self.request.headers.get("X-Phyzflow-Edit-Session") or "").split(",")
                    if part.strip()
                } or None
                try:
                    format_fn = getattr(discovered_sheets, "format_notice", None)
                    format_notice = format_fn() if format_fn is not None else workspace_format_notice(root)
                    current = discovered_sheets()
                except ValidationError as error:
                    format_notice = str(error)
                    with sheets_lock:
                        current = list(sheets)
                flows = []
                for sheet in current:
                    if sheet is None:
                        flows.append(None)
                        continue
                    status = sheet.status()
                    changed = None if status in ("running", "opening", "stopping") else sheet.changed_source(status)
                    entry = {
                        "name": sheet.title, "status": status, "deletable": can_delete_sheet(root, sheet.root),
                        "revision": sheet.revision, "changed_source": changed,
                        "edit_lock": sheet.edit_lock_status(viewer_sessions),
                        "recoverable": sheet.recovery_graph is not None,
                    }
                    if interpreter is not None:
                        attempt = sheet.attempt
                        entry["worker_ready"] = sheet.worker_ready()
                        entry["run_id"] = attempt.run_id if attempt is not None else None
                        entry["error"] = attempt.start_error if attempt is not None else None
                        entry["startup"] = sheet.startup
                    flows.append(entry)
                self.request.send_conditional_json({
                    "name": root.name, "flows": flows, "run_all": interpreter is not None,
                    "edit_running_source": interpreter is not None,
                    "format_notice": format_notice,
                }, vary="X-Phyzflow-Edit-Session")
                return
            view = self.view_route()
            if view is not None and is_flow_view_route("GET", view[2]):
                self.forward_view(*view)
                return
            asset = WORKSPACE_ASSETS.get(self.path)
            if asset is None:
                self.request.send_error(HTTPStatus.NOT_FOUND)
                return
            filename, content_type = asset
            content_type = content_type if ";" in content_type else f"{content_type}; charset=utf-8"
            self.request.send_static_asset(STATIC / filename, content_type)

        def do_POST(self) -> None:
            if not self.request.valid_post_origin():
                self.request.send_error(HTTPStatus.FORBIDDEN)
                return
            route, _, query = self.path.partition("?")
            if route in ("/files/folders", "/files/upload"):
                send_workspace_files(self.request, root, route, query, sheets_lock, is_closing)
                return
            if self.path == WORKSPACE_PLACEMENTS:
                self.workspace_placements()
                return
            if self.path == WORKSPACE_VISUALIZE_LAYOUT:
                self.visualize_layout()
                return
            if layout := VISUALIZE_LAYOUT_PATH.fullmatch(self.path):
                self.visualize_layout(int(layout[1]))
                return
            if node := RECOVERY_NODE_SOURCE.fullmatch(self.path):
                self.recovery_node(int(node[1]), int(node[2]), edit=True)
                return
            if interpreter is not None and (run := re.fullmatch(r"/flows/([0-9]{1,8})/run", self.path)):
                index = int(run[1])
                sheet = sheets[index] if index < len(sheets) else None
                if sheet is None:
                    self.request.send_json({"error": "This sheet is unavailable."}, HTTPStatus.NOT_FOUND)
                    return
                try:
                    sheet.start_run(None)
                except (FlowBusy, RuntimeError, OSError, ValueError) as error:
                    self.request.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
                else:
                    self.request.send_json({}, HTTPStatus.ACCEPTED)
                return
            if source := FLOW_SOURCE_PATH.fullmatch(self.path):
                self.flow_source(int(source[1]), source[2], source[3])
                return
            if lock_route := EDIT_LOCK_PATH.fullmatch(self.path):
                self.edit_lock(int(lock_route[1]))
                return
            if self.path == "/flows":
                self.create_sheet()
                return
            if self.path == "/run-all" and interpreter is not None:
                self.run_all()
                return
            view = self.view_route()
            if view is not None and is_flow_view_route("POST", view[2]):
                if dashboard := re.fullmatch(f"/{DASHBOARD_SOURCE_ROUTE}", view[2]):
                    self.sheet_dashboard_source(view[0], view[1], dashboard[1])
                    return
                if interpreter is not None and view[2] == "/run":
                    self.run_sheet(view[0], view[1])
                    return
                self.forward_view(*view)
                return
            command = re.fullmatch(r"/flows/([0-9]{1,8})/(open|stop|delete|rename|reload|reload/([0-9a-f]{64}))", self.path)
            if command is None or (index := int(command[1])) >= len(sheets):
                self.request.send_error(HTTPStatus.NOT_FOUND)
                return
            sheet = sheets[index]
            if command[2] == "delete":
                self.delete_sheet(index)
                return
            if sheet is None:
                self.request.send_json({"error": "This sheet has been deleted."}, HTTPStatus.NOT_FOUND)
                return
            if command[2] == "rename":
                self.rename_sheet(sheet)
                return
            if command[2] == "stop":
                try:
                    sheet.stop()
                except OSError as error:
                    self.request.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                else:
                    self.request.send_json({"status": "stopped"})
                return
            try:
                revision = sheet.open(reload=command[2].startswith("reload"), automatic_source=command[3])
                prefix = f"/revision/{revision}" if revision else ""
                self.request.send_json({
                    "url": f"/flows/{index}{prefix}/view/", "revision": revision, "status": sheet.status(),
                    "startup": sheet.startup,
                })
            except ReloadConflict as error:
                self.request.send_json({"error": str(error), "status": sheet.status()}, HTTPStatus.CONFLICT)
            except ReloadDeferred:
                self.request.send_json({"deferred": True}, HTTPStatus.CONFLICT)
            except ValidationError as error:
                self.request.send_json({"error": str(error), "status": sheet.status()}, HTTPStatus.BAD_REQUEST)
            except (OSError, RuntimeError, EOFError) as error:
                if interpreter is not None and not sheet.worker_ready() and not sheet.workspace_closing():
                    self.request.send_json({
                        "editor": True, "revision": sheet.revision, "status": sheet.status(), "startup": sheet.startup,
                    })
                else:
                    self.request.send_json({
                        "error": str(error) or "The flow process exited.", "revision": sheet.revision,
                    }, HTTPStatus.BAD_REQUEST)

    def dispatch(request: UIRequestHandler, path: str) -> None:
        if is_closing():
            request.send_json({"error": "Workspace is shutting down."}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        routes = Routes(request, path)
        if request.command == "GET":
            routes.do_GET()
        else:
            routes.do_POST()

    return dispatch
