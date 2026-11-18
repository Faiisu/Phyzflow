"""Host independent flow sheets using the existing local flow UI."""

from collections.abc import Callable
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from http import HTTPStatus
from http.client import HTTPConnection, HTTPException
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from subprocess import Popen, TimeoutExpired
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import sys
from threading import Lock, Thread
import traceback
from urllib.request import ProxyHandler, build_opener

from .execution import FlowBusy, sheet_busy, sheet_lock
from .project import ValidationError, load_project
from .sheets import (
    archive_sheet_directory,
    can_delete_sheet,
    create_sheet,
    discover_flows,
    edit_sheet_flow_source,
    source_stamp,
)
from .ui_settings import UISettings, ZoomSettings
from .web import FLOW_VIEW_SUBPATH_PATTERN, STATIC, UIRequestHandler, is_flow_view_route, ui_address


VIEW_PATH = re.compile(r"/flows/([0-9]{1,8})(?:/revision/([0-9]{1,8}))?/view" + FLOW_VIEW_SUBPATH_PATTERN)
FLOW_SOURCE_PATH = re.compile(r"/flows/([0-9]{1,8})/source")


def serve_flow(root: Path, zoom: ZoomSettings, publish: Callable[[dict], None]) -> None:
    from .web import serve

    try:
        sources: dict[Path, str] = {}
        with load_project(root, sources) as flow:
            serve(flow, root, 0, sources, zoom=zoom, on_ready=lambda address: publish({"url": address}))
    except ValidationError as error:
        publish({"error": str(error)})
    except Exception:
        traceback.print_exc()
        publish({"error": "Could not open the flow. Check the Workspace terminal for details."})


@dataclass(frozen=True)
class OpenFlow:
    process: Popen
    url: str


class ReloadConflict(RuntimeError):
    pass


class ReloadDeferred(RuntimeError):
    pass


def terminate(process: Popen) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=2)
    except TimeoutExpired:
        process.kill()
        process.wait()


class FlowSheet:
    def __init__(
        self, title: str, root: Path, zoom: ZoomSettings, *,
        main: bool = False, interpreter: Path | None = None,
    ):
        self.title = title
        self.root = root
        self.zoom = zoom
        self.worker: OpenFlow | None = None
        self.inactive_status = "ready"
        self.revision = 0
        self.closing = False
        self.lock = Lock()
        self.main = main
        self.interpreter = interpreter
        self.attempted_source: str | None = None

    def changed_source(self, status: str) -> str | None:
        attempted = self.attempted_source
        if attempted is None or status == "stopped":
            return None
        stamp = source_stamp(self.root, main=self.main)
        return stamp if stamp != attempted else None

    def open(self, *, reload: bool = False, automatic_source: str | None = None) -> int:
        with self.lock, ExitStack() as reservation:
            if self.closing:
                raise RuntimeError("Workspace is shutting down.")
            if reload:
                status = self.status()
                try:
                    reservation.enter_context(sheet_lock(self.root))
                except FlowBusy as error:
                    raise ReloadConflict(str(error)) from None
                if status == "running":
                    raise ReloadConflict("This flow is running. Wait for it to finish or stop it before reloading.")
                if status == "unavailable" and self.worker is not None and self.worker.process.poll() is None:
                    raise ReloadConflict("Could not verify the run status. Try reloading again or stop the flow first.")
                if automatic_source is not None and self.changed_source(status) != automatic_source:
                    raise ReloadDeferred
                self.stop_worker(status="opening")
                self.revision += 1
            if self.worker is not None:
                if self.worker.process.poll() is None:
                    return self.revision
                terminate(self.worker.process)
                self.worker = None

            self.attempted_source = automatic_source or source_stamp(self.root, main=self.main)
            self.inactive_status = "opening"
            try:
                self.worker = self.start_worker()
            except BaseException:
                self.inactive_status = "unavailable"
                raise
            return self.revision

    def start_worker(self) -> OpenFlow:
        with TemporaryDirectory(prefix="phyzflow-sheet-") as directory:
            ready = Path(directory) / "ready.json"
            worker = Path(__file__).with_name("sheet_worker.py")
            argv = [self.interpreter or sys.executable, "-Pu", worker, ready, json.dumps(self.zoom)]
            options = {}
            if self.interpreter is not None:
                argv.insert(1, "-I")
                options["env"] = {
                    **os.environ,
                    "PATH": f"{self.interpreter.parent}{os.pathsep}{os.environ.get('PATH', '')}",
                    "VIRTUAL_ENV": str(self.interpreter.parent.parent),
                }
            process = Popen(argv, cwd=self.root, **options)
            try:
                deadline = monotonic() + 10
                while not ready.is_file():
                    if process.poll() is not None and not ready.is_file():
                        raise RuntimeError("The flow process exited during startup.")
                    if monotonic() >= deadline:
                        raise RuntimeError("The flow did not start within ten seconds.")
                    sleep(.02)
                reply = json.loads(ready.read_text(encoding="utf-8"))
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                return OpenFlow(process, reply["url"])
            except BaseException:
                terminate(process)
                raise

    def stop(self, *, shutdown: bool = False) -> None:
        with self.lock:
            if shutdown:
                self.closing = True
            self.stop_worker()

    def stop_worker(self, *, status: str = "stopped") -> None:
        self.inactive_status = status
        if self.worker is not None:
            terminate(self.worker.process)
            self.worker = None

    def archive(self, destination: Path) -> None:
        with self.lock:
            self.stop_worker()
            self.root.rename(destination)
            self.closing = True

    def status(self) -> str:
        if sheet_busy(self.root):
            return "running"
        worker = self.worker
        if worker is None:
            return self.inactive_status
        if worker.process.poll() is None:
            try:
                with build_opener(ProxyHandler({})).open(f"{worker.url}/status", timeout=.5) as response:
                    return json.load(response)["status"]
            except (OSError, ValueError):
                pass
        return "unavailable"


class WorkspaceController:
    """In-process bridge so MCP/Agent jobs run and stop through the same FlowSheet worker as the UI."""

    def __init__(self, get_sheet: Callable[[Path], FlowSheet], shutdown_cb: Callable[[], None] | None = None) -> None:
        self._get_sheet = get_sheet
        self._shutdown = shutdown_cb

    def shutdown(self) -> None:
        if self._shutdown is not None:
            Thread(target=self._shutdown, daemon=True).start()

    def ensure_sheet_worker(self, sheet_path: Path) -> tuple[FlowSheet, str]:
        sheet = self._get_sheet(sheet_path)
        status = sheet.status()
        if status == "running":
            raise FlowBusy("Another flow run is active on this sheet. Wait for it to finish before starting another.")
        if sheet.worker is None or sheet.worker.process.poll() is not None:
            sheet.open(reload=False)
        elif sheet.changed_source(status) is not None:
            sheet.open(reload=True)
        worker = sheet.worker
        if worker is None or worker.process.poll() is not None:
            raise RuntimeError("Could not open the flow worker.")
        return sheet, worker.url

    def stop_sheet_run(self, sheet: FlowSheet) -> None:
        sheet.stop()
        if not sheet.closing:
            try:
                sheet.open(reload=False)
            except (OSError, RuntimeError, EOFError):
                pass


def workspace_routes(
    root: Path,
    flows: list[tuple[str, Path]],
    settings: UISettings,
    *,
    interpreter: Path | None = None,
):
    def make_sheet(title: str, path: Path) -> FlowSheet:
        return FlowSheet(title, path, settings.zoom, main=path == root, interpreter=interpreter)

    sheets: list[FlowSheet | None] = [make_sheet(title, path) for title, path in flows]
    sheets_lock = Lock()
    closing = False

    def resolve_sheet(sheet_path: Path) -> FlowSheet:
        target = sheet_path.resolve()
        with sheets_lock:
            if closing:
                raise RuntimeError("Workspace is shutting down.")
            for existing in sheets:
                if existing is not None and existing.root.resolve() == target:
                    return existing
            known = {sheet.root.resolve() for sheet in sheets if sheet is not None}
            for title, path in discover_flows(root):
                if path.resolve() not in known:
                    sheets.append(make_sheet(title, path))
            for existing in sheets:
                if existing is not None and existing.root.resolve() == target:
                    return existing
        raise FileNotFoundError(f"Unknown flow sheet: {sheet_path}")

    class Routes:
        def __init__(self, request: UIRequestHandler, path: str):
            self.request = request
            self.path = path

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
                with sheets_lock:
                    if closing:
                        self.request.send_json({"error": "Workspace is shutting down."}, HTTPStatus.SERVICE_UNAVAILABLE)
                        return
                    directory = create_sheet(root, value).parent
                    index = len(sheets)
                    sheets.append(make_sheet(directory.name, directory))
            except FileExistsError:
                self.request.send_json({"error": "That sheet name is already in use. Choose another name."}, HTTPStatus.CONFLICT)
                return
            except ValueError as error:
                self.request.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            except OSError:
                self.request.send_json({"error": "Could not write the flow file. Check the workspace folder permissions."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self.request.send_json({"index": index}, HTTPStatus.CREATED)

        def delete_sheet(self, index: int) -> None:
            with sheets_lock:
                sheet = sheets[index]
                if closing or sheet is None:
                    self.request.send_json({"error": "This sheet is no longer available."}, HTTPStatus.CONFLICT)
                    return
                if not can_delete_sheet(root, sheet.root):
                    self.request.send_json({"error": "Only sheets stored directly in this Workspace can be deleted."}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    archive_sheet_directory(root, sheet.root, sheet.archive)
                except OSError:
                    self.request.send_json({"error": "Could not move the flow folder to deleted flows."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                # Keep existing flow URLs tied to their original sheets.
                sheets[index] = None
            self.request.send_json({"status": "deleted"})

        def view_route(self) -> tuple[FlowSheet, int, str] | None:
            route = VIEW_PATH.fullmatch(self.path)
            if route is None or (index := int(route[1])) >= len(sheets):
                return None
            sheet = sheets[index]
            return (sheet, int(route[2] or "0"), route[3]) if sheet is not None else None

        def flow_source(self, index: int) -> None:
            source = revision = None
            if self.request.command == "POST":
                try:
                    if self.request.headers.get_content_type() != "application/json":
                        raise ValueError
                    data = self.request.read_json_dict()
                    source, revision = data.get("source"), data.get("revision")
                    if not isinstance(source, str) or not isinstance(revision, str):
                        raise ValueError
                except (ValueError, UnicodeError):
                    self.request.send_json({"error": "Send the source and revision as a JSON object up to 1 MiB."}, HTTPStatus.BAD_REQUEST)
                    return

            sheet = sheets[index] if index < len(sheets) else None
            if self.request.command == "POST" and sheet is not None and sheet.status() == "running":
                self.request.send_json({"error": "Cannot edit flow.py while the flow is running."}, HTTPStatus.CONFLICT)
                return

            def perform_edit() -> dict[str, str]:
                if sheet is None:
                    raise FileNotFoundError
                with sheet.lock:
                    if sheet.closing:
                        raise FileNotFoundError
                    return edit_sheet_flow_source(sheet.root, source, revision)

            self.request.send_source_edit(perform_edit, not_found_error="This sheet's flow.py is unavailable.")

        def forward_view(self, sheet: FlowSheet, revision: int, path: str) -> None:
            headers = {}
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
                    # A reload can begin after the worker accepts a run, while its response streams.
                    with sheet.lock if self.request.command == "POST" else nullcontext():
                        worker = sheet.worker
                        if revision != sheet.revision:
                            self.request.send_error(HTTPStatus.CONFLICT, "The flow has been reloaded. Refresh its sheet.")
                            return
                        if worker is None or worker.process.poll() is not None:
                            self.request.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Open the flow first.")
                            return
                        upstream = HTTPConnection(worker.url.removeprefix("http://"), timeout=2)
                        upstream.connect()
                        connection = upstream.sock
                        upstream.request(self.request.command, path, body=body, headers=headers)
                        response = upstream.getresponse()
                        # HTTP/1.0 can detach the socket from HTTPConnection after the headers.
                        if connection is not None:
                            connection.settimeout(None)
                except (OSError, HTTPException):
                    self.request.send_error(HTTPStatus.BAD_GATEWAY, "The flow process is unavailable.")
                    return

                with response:
                    try:
                        self.request.send_response(response.status)
                        for name in ("Content-Type", "Content-Length", "Cache-Control"):
                            value = response.getheader(name)
                            if value is not None:
                                self.request.send_header(name, value)
                        self.request.end_headers()
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
            if source := FLOW_SOURCE_PATH.fullmatch(self.path):
                self.flow_source(int(source[1]))
                return
            if self.path == "/workspace":
                with sheets_lock:
                    if not closing:
                        known = {sheet.root for sheet in sheets if sheet is not None}
                        sheets.extend(
                            make_sheet(title, path)
                            for title, path in discover_flows(root)
                            if path not in known
                        )
                    current = list(sheets)
                flows = []
                for sheet in current:
                    if sheet is None:
                        flows.append(None)
                        continue
                    status = sheet.status()
                    flows.append({
                        "name": sheet.title, "status": status, "deletable": can_delete_sheet(root, sheet.root),
                        "revision": sheet.revision, "changed_source": sheet.changed_source(status),
                    })
                self.request.send_json({"name": root.name, "flows": flows})
                return
            view = self.view_route()
            if view is not None and is_flow_view_route("GET", view[2]):
                self.forward_view(*view)
                return
            asset = {
                "/": ("workspace.html", "text/html"),
                "/workspace.css": ("workspace.css", "text/css"),
                "/workspace.js": ("workspace.js", "text/javascript"),
            }.get(self.path)
            if asset is None:
                self.request.send_error(HTTPStatus.NOT_FOUND)
                return
            filename, content_type = asset
            self.request.send_bytes((STATIC / filename).read_bytes(), f"{content_type}; charset=utf-8")

        def do_POST(self) -> None:
            if not self.request.valid_post_origin():
                self.request.send_error(HTTPStatus.FORBIDDEN)
                return
            if source := FLOW_SOURCE_PATH.fullmatch(self.path):
                self.flow_source(int(source[1]))
                return
            if self.path == "/flows":
                self.create_sheet()
                return
            view = self.view_route()
            if view is not None and is_flow_view_route("POST", view[2]):
                self.forward_view(*view)
                return
            command = re.fullmatch(r"/flows/([0-9]{1,8})/(open|stop|delete|reload|reload/([0-9a-f]{64}))", self.path)
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
            if command[2] == "stop":
                sheet.stop()
                self.request.send_json({"status": "stopped"})
                return
            try:
                revision = sheet.open(reload=command[2].startswith("reload"), automatic_source=command[3])
                prefix = f"/revision/{revision}" if revision else ""
                self.request.send_json({"url": f"/flows/{index}{prefix}/view/", "revision": revision})
            except ReloadConflict as error:
                self.request.send_json({"error": str(error), "status": sheet.status()}, HTTPStatus.CONFLICT)
            except ReloadDeferred:
                self.request.send_json({"deferred": True}, HTTPStatus.CONFLICT)
            except (OSError, RuntimeError, EOFError) as error:
                self.request.send_json({"error": str(error) or "The flow process exited."}, HTTPStatus.BAD_REQUEST)

    def dispatch(request: UIRequestHandler, path: str) -> None:
        routes = Routes(request, path)
        if request.command == "GET":
            routes.do_GET()
        else:
            routes.do_POST()

    def close() -> None:
        nonlocal closing
        with sheets_lock:
            closing = True
        for sheet in sheets:
            if sheet is not None:
                sheet.stop(shutdown=True)

    return dispatch, close, resolve_sheet


def serve_workspace(
    root: Path,
    flows: list[tuple[str, Path]],
    settings: UISettings,
    *,
    on_controller: Callable[[WorkspaceController], None] | None = None,
) -> None:
    dispatch, close, resolve_sheet = workspace_routes(root, flows, settings)

    class Handler(UIRequestHandler):
        def do_GET(self) -> None:
            dispatch(self, self.path)

        do_POST = do_GET

    try:
        with ThreadingHTTPServer((settings.ipv4, settings.port), Handler) as server:
            if on_controller is not None:
                on_controller(WorkspaceController(resolve_sheet, server.shutdown))
            print(f"Workspace UI: {ui_address(settings.ipv4, server.server_port)}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        close()
