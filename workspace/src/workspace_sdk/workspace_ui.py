"""Host independent flow sheets using the existing local flow UI."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
from threading import Lock, RLock, Thread
import traceback
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from .execution import FlowBusy, sheet_busy, sheet_lock
from .project import ValidationError, load_project
from .sheet_runs import SheetRun
from .sheets import (
    archive_sheet_directory,
    can_delete_sheet,
    create_sheet,
    discover_flows,
    edit_sheet_flow_source,
    sheet_source_files,
    source_stamp,
)
from .ui_settings import UISettings, ZoomSettings
from .web import FLOW_VIEW_SUBPATH_PATTERN, STATIC, UIRequestHandler, is_flow_view_route, sample_input, ui_address


VIEW_PATH = re.compile(r"/flows/([0-9]{1,8})(?:/revision/([0-9]{1,8}))?/view" + FLOW_VIEW_SUBPATH_PATTERN)
FLOW_SOURCE_PATH = re.compile(r"/flows/([0-9]{1,8})/source")


def serve_flow(
    root: Path, zoom: ZoomSettings, publish: Callable[[dict], None], *,
    run_token: str | None = None, main: bool = False,
) -> None:
    from .web import serve

    try:
        sources: dict[Path, str] = {}
        snapshot_sources = sheet_source_files(root, main=main) if run_token is not None else ()
        with load_project(root, sources, snapshot_sources=snapshot_sources) as flow:
            serve(
                flow, root, 0, sources, zoom=zoom, run_token=run_token,
                on_ready=lambda address: publish({"url": address}),
            )
    except ValidationError as error:
        publish({"error": str(error)})
    except Exception:
        if run_token is None:
            traceback.print_exc()
            publish({"error": "Could not open the flow. Check the Workspace terminal for details."})
        else:
            publish({"error": "Could not open the flow. Check the flow source and Workspace environment."})


@dataclass
class OpenFlow:
    process: Popen
    url: str = ""
    run_token: str | None = None
    lifeline: int | None = None

    def stop(self) -> None:
        if self.run_token is None or os.name != "posix":
            terminate(self.process)
            return
        if self.lifeline is not None:
            os.close(self.lifeline)
            self.lifeline = None
        try:
            self.process.wait(timeout=4)
        except TimeoutExpired:
            raise OSError("Could not confirm that the sheet process stopped.") from None


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
        self.lock = RLock()
        self.main = main
        self.interpreter = interpreter
        self.attempted_source: str | None = None
        self.attempt: SheetRun | None = None

    def changed_source(self, status: str) -> str | None:
        attempted = self.attempted_source
        if attempted is None or status == "stopped":
            return None
        stamp = source_stamp(self.root, main=self.main)
        return stamp if stamp != attempted else None

    def open(
        self, *, reload: bool = False, automatic_source: str | None = None, attempt: SheetRun | None = None,
    ) -> int:
        with self.lock, ExitStack() as reservation:
            if self.closing:
                raise RuntimeError("Workspace is shutting down.")
            if reload:
                active = self.attempt
                if active is not None and active is not attempt and active.status == "running":
                    raise ReloadConflict("This flow is running. Wait for it to finish or stop it before reloading.")
                status = self.worker_status()
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
                if self.worker.url and self.worker.process.poll() is None and self.inactive_status != "stopping":
                    return self.revision
                self.stop_worker(status="opening")
                if self.interpreter is not None:
                    self.revision += 1

            if attempt is None and self.attempt is not None and self.attempt.status != "running":
                self.attempt = None
            self.attempted_source = automatic_source or source_stamp(self.root, main=self.main)
            self.inactive_status = "opening"
            try:
                self.start_worker()
            except BaseException:
                self.inactive_status = "unavailable"
                raise
            self.inactive_status = "ready"
            return self.revision

    def start_run(self, inputs: dict | None, *, revision: int | None = None) -> SheetRun:
        attempt = self.attempt
        if attempt is not None and attempt.status == "running":
            raise FlowBusy("A run is already in progress on this sheet.")
        with self.lock:
            if self.closing:
                raise RuntimeError("Workspace is shutting down.")
            attempt = self.attempt
            if (attempt is not None and attempt.status == "running") or sheet_busy(self.root):
                raise FlowBusy("A run is already in progress on this sheet.")
            if revision is not None and revision != self.revision:
                raise ReloadConflict("The flow has been reloaded. Refresh its sheet.")
            attempt = self.attempt = SheetRun(inputs)
            Thread(target=self.collect_run, args=(attempt,), daemon=True).start()
            return attempt

    def collect_run(self, attempt: SheetRun) -> None:
        try:
            with self.lock:
                if self.closing or self.attempt is not attempt or attempt.status != "running":
                    return
                worker = self.worker
                reload = (
                    worker is not None and worker.url and worker.process.poll() is None
                    and self.changed_source(self.inactive_status) is not None
                )
                if attempt.inputs is None:
                    attempt.inputs = sample_input(self.root)
                self.open(reload=bool(reload), attempt=attempt)
                worker = self.worker
            status, error = attempt.collect(worker.url, worker.run_token)
        except (OSError, RuntimeError, ValueError) as failure:
            status, error = "failed", str(failure)
        with self.lock:
            if self.attempt is attempt:
                attempt.finish(status, error)

    def start_worker(self) -> None:
        with TemporaryDirectory(prefix="phyzflow-sheet-") as directory:
            ready = Path(directory) / "ready.json"
            worker = Path(__file__).with_name("sheet_worker.py")
            argv = [self.interpreter or sys.executable, "-Pu", worker, ready, json.dumps(self.zoom)]
            options = {}
            token = None
            if self.interpreter is not None:
                argv.insert(1, "-I")
                token = uuid4().hex
                argv.extend((token, str(int(self.main))))
                options["env"] = {
                    **os.environ,
                    "PATH": f"{self.interpreter.parent}{os.pathsep}{os.environ.get('PATH', '')}",
                    "VIRTUAL_ENV": str(self.interpreter.parent.parent),
                }
            lifeline = None
            if token is not None and os.name == "posix":
                reader, lifeline = os.pipe()
                supervisor = Path(__file__).with_name("agent_supervisor.py")
                try:
                    process = Popen(
                        [sys.executable, "-I", supervisor, str(reader), "-1", "-1", *argv],
                        cwd=self.root, start_new_session=True, pass_fds=(reader,), **options,
                    )
                except BaseException:
                    os.close(lifeline)
                    raise
                finally:
                    os.close(reader)
            else:
                process = Popen(argv, cwd=self.root, **options)
            worker = self.worker = OpenFlow(process, run_token=token, lifeline=lifeline)
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
                worker.url = reply["url"]
            except BaseException:
                self.stop_worker(status="unavailable")
                raise

    def stop(self, *, shutdown: bool = False) -> None:
        with self.lock:
            if shutdown:
                self.closing = True
            self.stop_run()

    def stop_run(self) -> None:
        if self.interpreter is not None:
            self.inactive_status = "stopping"
        if self.attempt is not None:
            self.attempt.finish("stopped")
        self.stop_worker()
        if self.interpreter is not None:
            self.revision += 1

    def stop_worker(self, *, status: str = "stopped") -> None:
        if self.worker is not None:
            self.inactive_status = "stopping"
            self.worker.stop()
            self.worker = None
        self.inactive_status = status

    def archive(self, destination: Path) -> None:
        with self.lock:
            self.stop_run()
            self.root.rename(destination)
            self.closing = True

    def status(self) -> str:
        attempt = self.attempt
        if self.inactive_status == "stopping":
            return "stopping"
        if attempt is not None and (attempt.status == "running" or self.inactive_status != "stopped"):
            return attempt.status
        return self.worker_status()

    def worker_status(self) -> str:
        if not self.closing and sheet_busy(self.root):
            return "running"
        worker = self.worker
        if worker is None:
            return self.inactive_status
        if worker.url and worker.process.poll() is None:
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

    def discovered_sheets() -> list[FlowSheet | None]:
        with sheets_lock:
            if not closing:
                known = {sheet.root for sheet in sheets if sheet is not None}
                sheets.extend(
                    make_sheet(title, path)
                    for title, path in discover_flows(root)
                    if path not in known
                )
            return list(sheets)

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
            if source is not None and interpreter is None and sheet is not None and sheet.status() == "running":
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
            current = {index: sheet for index, sheet in enumerate(discovered_sheets()) if sheet is not None}
            started, errors = {}, {}
            with ThreadPoolExecutor() as pool:
                pending = {index: pool.submit(sheet.start_run, None) for index, sheet in current.items()}
                for index, admission in pending.items():
                    try:
                        started[index] = admission.result().run_id
                    except FlowBusy:
                        pass
                    except (OSError, RuntimeError, ValueError) as error:
                        errors[index] = str(error)
            self.request.send_json({"started": started, "errors": errors}, HTTPStatus.ACCEPTED)

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
                current = discovered_sheets()
                flows = []
                for sheet in current:
                    if sheet is None:
                        flows.append(None)
                        continue
                    status = sheet.status()
                    entry = {
                        "name": sheet.title, "status": status, "deletable": can_delete_sheet(root, sheet.root),
                        "revision": sheet.revision, "changed_source": sheet.changed_source(status),
                    }
                    if interpreter is not None:
                        attempt = sheet.attempt
                        entry["run_id"] = attempt.run_id if attempt is not None else None
                        entry["error"] = attempt.start_error if attempt is not None else None
                    flows.append(entry)
                self.request.send_json({
                    "name": root.name, "flows": flows, "run_all": interpreter is not None,
                    "edit_running_source": interpreter is not None,
                })
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
            if self.path == "/run-all" and interpreter is not None:
                self.run_all()
                return
            view = self.view_route()
            if view is not None and is_flow_view_route("POST", view[2]):
                if interpreter is not None and view[2] == "/run":
                    self.run_sheet(view[0], view[1])
                    return
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
                self.request.send_json({"url": f"/flows/{index}{prefix}/view/", "revision": revision})
            except ReloadConflict as error:
                self.request.send_json({"error": str(error), "status": sheet.status()}, HTTPStatus.CONFLICT)
            except ReloadDeferred:
                self.request.send_json({"deferred": True}, HTTPStatus.CONFLICT)
            except (OSError, RuntimeError, EOFError) as error:
                self.request.send_json({"error": str(error) or "The flow process exited."}, HTTPStatus.BAD_REQUEST)

    def dispatch(request: UIRequestHandler, path: str) -> None:
        if closing:
            request.send_json({"error": "Workspace is shutting down."}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        routes = Routes(request, path)
        if request.command == "GET":
            routes.do_GET()
        else:
            routes.do_POST()

    def close() -> None:
        nonlocal closing
        with sheets_lock:
            closing = True
        with ThreadPoolExecutor() as pool:
            pending = [pool.submit(sheet.stop, shutdown=True) for sheet in sheets if sheet is not None]
            for stop in pending:
                stop.result()

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
