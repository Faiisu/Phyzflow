"""Host independent flow sheets using the existing local flow UI."""

from contextlib import nullcontext
from dataclasses import dataclass
from hashlib import sha256
from http import HTTPStatus
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import multiprocessing
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
import os
from pathlib import Path
import re
from threading import Lock
import traceback
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from .node_sources import SourceChanged, edit_source_file
from .project import ValidationError, load_project
from .sheets import create_sheet, discover_flows
from .ui_settings import UISettings, ZoomSettings
from .web import STATIC, matches_ui_host, serve, ui_address


VIEW_PATH = re.compile(
    r"/flows/([0-9]{1,8})(?:/revision/([0-9]{1,8}))?/view"
    r"(/(?:app\.css|app\.js|dashboard-ui\.js|edge-routing\.js|prism-syntax\.js|project|"
    r"dashboard-(?:assets|settings)/(?:workflow|[0-9]{1,8})|"
    r"node-(?:files|source)/[0-9]{1,8}|history(?:/[0-9a-f]{32})?|run)?)"
)
FLOW_SOURCE_PATH = re.compile(r"/flows/([0-9]{1,8})/source")


def serve_flow(root: Path, zoom: ZoomSettings, ready: Connection) -> None:
    def report_address(address: str) -> None:
        ready.send({"url": address})
        ready.close()

    try:
        os.chdir(root)
        sources: dict[Path, str] = {}
        with load_project(root, sources) as flow:
            serve(flow, root, 0, sources, zoom=zoom, on_ready=report_address)
    except ValidationError as error:
        if not ready.closed:
            ready.send({"error": str(error)})
    except Exception:
        traceback.print_exc()
        if not ready.closed:
            ready.send({"error": "Could not open the flow. Check the Workspace terminal for details."})
    finally:
        ready.close()


@dataclass(frozen=True)
class OpenFlow:
    process: BaseProcess
    url: str


class ReloadConflict(RuntimeError):
    pass


class ReloadDeferred(RuntimeError):
    pass


def source_stamp(root: Path, *, main: bool) -> str:
    # Track the sheet's Python tree, including new helpers that cannot be imported yet.
    entries = []
    for directory, folders, files in os.walk(root):
        path = Path(directory)
        folders[:] = [
            name for name in folders
            if not name.startswith(".") and name != "__pycache__"
            and not (main and path == root and name == "flows")
            and not (path / name / "pyvenv.cfg").is_file()
        ]
        for name in files:
            if not name.endswith(".py") and not (path == root and name == "pyproject.toml"):
                continue
            source = path / name
            try:
                stat = source.stat(follow_symlinks=False)
            except OSError:
                continue
            entries.append((str(source.relative_to(root)), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    return sha256(json.dumps(sorted(entries)).encode()).hexdigest()


def terminate(process: BaseProcess) -> None:
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
    process.join()


class FlowSheet:
    def __init__(self, title: str, root: Path, zoom: ZoomSettings, *, main: bool = False):
        self.title = title
        self.root = root
        self.zoom = zoom
        self.worker: OpenFlow | None = None
        self.inactive_status = "ready"
        self.revision = 0
        self.closing = False
        self.lock = Lock()
        self.main = main
        self.attempted_source: str | None = None

    def changed_source(self, status: str) -> str | None:
        attempted = self.attempted_source
        if attempted is None or status == "stopped":
            return None
        stamp = source_stamp(self.root, main=self.main)
        return stamp if stamp != attempted else None

    def open(self, *, reload: bool = False, automatic_source: str | None = None) -> int:
        with self.lock:
            if self.closing:
                raise RuntimeError("Workspace is shutting down.")
            if reload:
                status = self.status()
                if status == "running":
                    raise ReloadConflict("This flow is running. Wait for it to finish or stop it before reloading.")
                if status == "unavailable" and self.worker is not None and self.worker.process.is_alive():
                    raise ReloadConflict("Could not verify the run status. Try reloading again or stop the flow first.")
                if automatic_source is not None and self.changed_source(status) != automatic_source:
                    raise ReloadDeferred
                self.stop_worker(status="opening")
                self.revision += 1
            if self.worker is not None:
                if self.worker.process.is_alive():
                    return self.revision
                terminate(self.worker.process)
                self.worker = None

            self.attempted_source = automatic_source or source_stamp(self.root, main=self.main)
            self.inactive_status = "opening"
            context = multiprocessing.get_context("spawn")
            incoming, outgoing = context.Pipe(duplex=False)
            process = context.Process(target=serve_flow, args=(self.root, self.zoom, outgoing))
            with incoming, outgoing:
                try:
                    process.start()
                    outgoing.close()
                    if not incoming.poll(10):
                        raise RuntimeError("The flow did not start within ten seconds.")
                    reply = incoming.recv()
                    if "error" in reply:
                        raise RuntimeError(reply["error"])
                except BaseException:
                    self.inactive_status = "unavailable"
                    if process.pid is not None:
                        terminate(process)
                    raise
            self.worker = OpenFlow(process, reply["url"])
            return self.revision

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
        worker = self.worker
        if worker is None:
            return self.inactive_status
        if worker.process.is_alive():
            try:
                with build_opener(ProxyHandler({})).open(f"{worker.url}/status", timeout=.5) as response:
                    return json.load(response)["status"]
            except (OSError, ValueError):
                pass
        return "unavailable"


def serve_workspace(root: Path, flows: list[tuple[str, Path]], settings: UISettings) -> None:
    def make_sheet(title: str, path: Path) -> FlowSheet:
        return FlowSheet(title, path, settings.zoom, main=path == root)

    sheets: list[FlowSheet | None] = [make_sheet(title, path) for title, path in flows]
    sheets_lock = Lock()
    closing = False

    def can_delete(sheet: FlowSheet) -> bool:
        return sheet.root.parent == root / "flows" and not sheet.root.parent.is_symlink() and not sheet.root.is_symlink()

    class Handler(BaseHTTPRequestHandler):
        def log_request(self, code="-", size="-"):
            if self.path != "/workspace":
                super().log_request(code, size)

        def send_bytes(self, data: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, data: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_bytes(json.dumps(data).encode(), "application/json", status)

        def local_request(self) -> bool:
            return matches_ui_host(self)

        def create_sheet(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096 or self.headers.get_content_type() != "application/json":
                    raise ValueError
                data = json.loads(self.rfile.read(size))
            except ValueError:
                self.send_json({"error": "Send a JSON object with a sheet name."}, HTTPStatus.BAD_REQUEST)
                return
            value = data.get("name") if isinstance(data, dict) else None

            try:
                with sheets_lock:
                    if closing:
                        self.send_json({"error": "Workspace is shutting down."}, HTTPStatus.SERVICE_UNAVAILABLE)
                        return
                    directory = create_sheet(root, value).parent
                    index = len(sheets)
                    sheets.append(make_sheet(directory.name, directory))
            except FileExistsError:
                self.send_json({"error": "That sheet name is already in use. Choose another name."}, HTTPStatus.CONFLICT)
                return
            except ValueError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            except OSError:
                self.send_json({"error": "Could not write the flow file. Check the workspace folder permissions."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self.send_json({"index": index}, HTTPStatus.CREATED)

        def delete_sheet(self, index: int) -> None:
            trash = root / ".workspace" / "deleted-flows"
            with sheets_lock:
                sheet = sheets[index]
                if closing or sheet is None:
                    self.send_json({"error": "This sheet is no longer available."}, HTTPStatus.CONFLICT)
                    return
                if not can_delete(sheet):
                    self.send_json({"error": "Only sheets stored directly in this Workspace can be deleted."}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    if trash.parent.is_symlink() or trash.is_symlink():
                        raise OSError
                    archive = trash / uuid4().hex
                    archive.mkdir(parents=True)
                    try:
                        sheet.archive(archive / sheet.root.name)
                    except OSError:
                        archive.rmdir()
                        raise
                except OSError:
                    self.send_json({"error": "Could not move the flow folder to deleted flows."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                # Keep existing flow URLs tied to their original sheets.
                sheets[index] = None
            self.send_json({"status": "deleted"})

        def view_route(self) -> tuple[FlowSheet, int, str] | None:
            route = VIEW_PATH.fullmatch(self.path)
            if route is None or (index := int(route[1])) >= len(sheets):
                return None
            sheet = sheets[index]
            return (sheet, int(route[2] or "0"), route[3]) if sheet is not None else None

        def flow_source(self, index: int) -> None:
            source = revision = None
            if self.command == "POST":
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 1024 * 1024 or self.headers.get_content_type() != "application/json":
                        raise ValueError
                    data = json.loads(self.rfile.read(size))
                    if not isinstance(data, dict):
                        raise ValueError
                    source, revision = data.get("source"), data.get("revision")
                    if not isinstance(source, str) or not isinstance(revision, str):
                        raise ValueError
                except ValueError:
                    self.send_json({"error": "Send the source and revision as a JSON object up to 1 MiB."}, HTTPStatus.BAD_REQUEST)
                    return

            sheet = sheets[index] if index < len(sheets) else None
            try:
                if sheet is None:
                    raise FileNotFoundError
                with sheet.lock:
                    path = sheet.root / "flow.py"
                    if sheet.closing or path.resolve() != path or not path.is_file():
                        raise FileNotFoundError
                    result = edit_source_file(path, source, revision)
            except FileNotFoundError:
                self.send_json({"error": "This sheet's flow.py is unavailable."}, HTTPStatus.NOT_FOUND)
            except SourceChanged:
                self.send_json({"error": "This file changed on disk. Reopen it before saving."}, HTTPStatus.CONFLICT)
            except (UnicodeError, SyntaxError):
                self.send_json({"error": "Could not read or write the file in its original encoding."}, HTTPStatus.BAD_REQUEST)
            except ValueError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except (OSError, RuntimeError):
                self.send_json({"error": "Could not read or write the file."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self.send_json(result)

        def forward_view(self, sheet: FlowSheet, revision: int, path: str) -> None:
            headers = {}
            body = None
            if self.command == "POST":
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 1024 * 1024:
                        raise ValueError
                except ValueError:
                    self.send_error(HTTPStatus.BAD_REQUEST, "Send a JSON object up to 1 MiB.")
                    return
                body = self.rfile.read(size)
                headers["Content-Type"] = self.headers.get("Content-Type", "")

            upstream = None
            try:
                try:
                    # A reload can begin after the worker accepts a run, while its response streams.
                    with sheet.lock if self.command == "POST" else nullcontext():
                        worker = sheet.worker
                        if revision != sheet.revision:
                            self.send_error(HTTPStatus.CONFLICT, "The flow has been reloaded. Refresh its sheet.")
                            return
                        if worker is None or not worker.process.is_alive():
                            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Open the flow first.")
                            return
                        upstream = HTTPConnection(worker.url.removeprefix("http://"), timeout=2)
                        upstream.connect()
                        connection = upstream.sock
                        upstream.request(self.command, path, body=body, headers=headers)
                        response = upstream.getresponse()
                        # HTTP/1.0 can detach the socket from HTTPConnection after the headers.
                        if connection is not None:
                            connection.settimeout(None)
                except (OSError, HTTPException):
                    self.send_error(HTTPStatus.BAD_GATEWAY, "The flow process is unavailable.")
                    return

                with response:
                    try:
                        self.send_response(response.status)
                        for name in ("Content-Type", "Content-Length", "Cache-Control"):
                            value = response.getheader(name)
                            if value is not None:
                                self.send_header(name, value)
                        self.end_headers()
                        while chunk := response.read1(65536):
                            self.wfile.write(chunk)
                    except (OSError, HTTPException):
                        return
            finally:
                if upstream is not None:
                    upstream.close()

        def do_GET(self) -> None:
            if not self.local_request():
                self.send_error(HTTPStatus.FORBIDDEN)
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
                        "name": sheet.title, "status": status, "deletable": can_delete(sheet),
                        "revision": sheet.revision, "changed_source": sheet.changed_source(status),
                    })
                self.send_json({"name": root.name, "flows": flows})
                return
            view = self.view_route()
            if view is not None and view[2] != "/run" and not view[2].startswith("/node-source/"):
                self.forward_view(*view)
                return
            asset = {
                "/": ("workspace.html", "text/html"),
                "/workspace.css": ("workspace.css", "text/css"),
                "/workspace.js": ("workspace.js", "text/javascript"),
            }.get(self.path)
            if asset is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            filename, content_type = asset
            self.send_bytes((STATIC / filename).read_bytes(), f"{content_type}; charset=utf-8")

        def do_POST(self) -> None:
            origin = self.headers.get("Origin")
            if not self.local_request() or origin not in (None, f"http://{self.headers.get('Host')}"):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if source := FLOW_SOURCE_PATH.fullmatch(self.path):
                self.flow_source(int(source[1]))
                return
            if self.path == "/flows":
                self.create_sheet()
                return
            view = self.view_route()
            if view is not None and (view[2] == "/run" or view[2].startswith(("/node-source/", "/dashboard-settings/"))):
                self.forward_view(*view)
                return
            command = re.fullmatch(r"/flows/([0-9]{1,8})/(open|stop|delete|reload|reload/([0-9a-f]{64}))", self.path)
            if command is None or (index := int(command[1])) >= len(sheets):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            sheet = sheets[index]
            if command[2] == "delete":
                self.delete_sheet(index)
                return
            if sheet is None:
                self.send_json({"error": "This sheet has been deleted."}, HTTPStatus.NOT_FOUND)
                return
            if command[2] == "stop":
                sheet.stop()
                self.send_json({"status": "stopped"})
                return
            try:
                revision = sheet.open(reload=command[2].startswith("reload"), automatic_source=command[3])
                prefix = f"/revision/{revision}" if revision else ""
                self.send_json({"url": f"/flows/{index}{prefix}/view/", "revision": revision})
            except ReloadConflict as error:
                self.send_json({"error": str(error), "status": sheet.status()}, HTTPStatus.CONFLICT)
            except ReloadDeferred:
                self.send_json({"deferred": True}, HTTPStatus.CONFLICT)
            except (OSError, RuntimeError, EOFError) as error:
                self.send_json({"error": str(error) or "The flow process exited."}, HTTPStatus.BAD_REQUEST)

    try:
        with ThreadingHTTPServer((settings.ipv4, settings.port), Handler) as server:
            print(f"Workspace UI: {ui_address(settings.ipv4, server.server_port)}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        with sheets_lock:
            closing = True
        for sheet in sheets:
            if sheet is not None:
                sheet.stop(shutdown=True)
