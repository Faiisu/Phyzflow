"""Host independent flow sheets using the existing local flow UI."""

from dataclasses import dataclass
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

from .project import ValidationError, load_project
from .ui_settings import UISettings, ZoomSettings
from .web import STATIC, serve


VIEW_PATH = re.compile(r"/flows/([0-9]{1,8})/view(/(?:app\.css|app\.js|project|history(?:/[0-9a-f]{32})?|run)?)")


def discover_flows(root: Path) -> list[tuple[str, Path]]:
    folder = root / "flows"
    flows = [(path.name, path) for path in sorted(folder.iterdir()) if (path / "flow.py").is_file()]
    if flows and (root / "flow.py").is_file():
        flows.insert(0, ("Main", root))
    return flows


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


def terminate(process: BaseProcess) -> None:
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
    process.join()


class FlowSheet:
    def __init__(self, title: str, root: Path, zoom: ZoomSettings):
        self.title = title
        self.root = root
        self.zoom = zoom
        self.worker: OpenFlow | None = None
        self.inactive_status = "ready"
        self.closing = False
        self.lock = Lock()

    def open(self) -> str:
        with self.lock:
            if self.closing:
                raise RuntimeError("Workspace is shutting down.")
            if self.worker is not None:
                if self.worker.process.is_alive():
                    return self.worker.url
                terminate(self.worker.process)
                self.worker = None

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
            return self.worker.url

    def stop(self, *, shutdown: bool = False) -> None:
        with self.lock:
            if shutdown:
                self.closing = True
            self.inactive_status = "stopped"
            if self.worker is not None:
                terminate(self.worker.process)
                self.worker = None

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
    sheets = [FlowSheet(title, path, settings.zoom) for title, path in flows]

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
            return self.headers.get("Host") == f"127.0.0.1:{server.server_port}"

        def view_route(self) -> tuple[FlowSheet, str] | None:
            route = VIEW_PATH.fullmatch(self.path)
            if route is None or (index := int(route[1])) >= len(sheets):
                return None
            return sheets[index], route[2]

        def forward_view(self, sheet: FlowSheet, path: str) -> None:
            worker = sheet.worker
            if worker is None or not worker.process.is_alive():
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Open the flow first.")
                return

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

            upstream = HTTPConnection(worker.url.removeprefix("http://"), timeout=2)
            try:
                try:
                    upstream.connect()
                    if upstream.sock is not None:
                        upstream.sock.settimeout(None)
                    upstream.request(self.command, path, body=body, headers=headers)
                    response = upstream.getresponse()
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
                upstream.close()

        def do_GET(self) -> None:
            if not self.local_request():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if self.path == "/workspace":
                self.send_json({
                    "name": root.name,
                    "flows": [{"name": sheet.title, "status": sheet.status()} for sheet in sheets],
                })
                return
            view = self.view_route()
            if view is not None and view[1] != "/run":
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
            view = self.view_route()
            if view is not None and view[1] == "/run":
                self.forward_view(*view)
                return
            command = re.fullmatch(r"/flows/([0-9]{1,8})/(open|stop)", self.path)
            if command is None or (index := int(command[1])) >= len(sheets):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            sheet = sheets[index]
            if command[2] == "stop":
                sheet.stop()
                self.send_json({"status": "stopped"})
                return
            try:
                sheet.open()
                self.send_json({"url": f"/flows/{index}/view/"})
            except (OSError, RuntimeError, EOFError) as error:
                self.send_json({"error": str(error) or "The flow process exited."}, HTTPStatus.BAD_REQUEST)

    try:
        with ThreadingHTTPServer(("127.0.0.1", settings.port), Handler) as server:
            print(f"Workspace UI: http://127.0.0.1:{server.server_port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        for sheet in sheets:
            sheet.stop(shutdown=True)
