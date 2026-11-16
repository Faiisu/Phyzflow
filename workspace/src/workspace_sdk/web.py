from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any

from .flow import Flow, Output
from .history import RunHistory
from .project import ordered_steps, relative_source, source_location
from .runner import run


STATIC = Path(__file__).with_name("static")


def serve(flow: Flow, root: Path, port: int, loaded_sources: dict[Path, str]) -> None:
    graph = []
    for step in ordered_steps(flow):
        file, line = source_location(step.function)
        graph.append({
            "id": step.id,
            "file": file,
            "line": line,
            "dependencies": [
                ref.step_id for ref in step.arguments.values() if isinstance(ref, Output)
            ],
        })

    fixture = root / "sample.json"
    default_input = json.loads(fixture.read_text())["input"] if fixture.is_file() else {}
    history = RunHistory(root)
    run_lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, data, content_type):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def local_request(self):
            return self.headers.get("Host") == f"127.0.0.1:{server.server_port}"

        def do_GET(self):
            if not self.local_request():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            asset = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.css": ("app.css", "text/css; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            }.get(self.path)
            if asset:
                name, content_type = asset
                self.send_bytes((STATIC / name).read_bytes(), content_type)
            elif self.path == "/project":
                data = json.dumps({"steps": graph, "input": default_input}).encode()
                self.send_bytes(data, "application/json")
            elif self.path == "/history" or self.path.startswith("/history/"):
                try:
                    data = history.list_runs() if self.path == "/history" else history.read(
                        self.path.removeprefix("/history/")
                    )
                except (OSError, ValueError):
                    self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not read run history.")
                    return
                if data is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                else:
                    self.send_bytes(json.dumps(data).encode(), "application/json")
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self.local_request() or origin not in (None, f"http://{self.headers.get('Host')}"):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if self.path != "/run":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if self.headers.get_content_type() != "application/json":
                self.send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError
                inputs = json.loads(self.rfile.read(size))
                if not isinstance(inputs, dict):
                    raise ValueError
            except (ValueError, UnicodeError):
                self.send_error(HTTPStatus.BAD_REQUEST, "Send a JSON object up to 1 MiB.")
                return

            if not run_lock.acquire(blocking=False):
                self.send_error(HTTPStatus.CONFLICT, "A run is already in progress.")
                return

            try:
                self.stream_run(inputs)
            finally:
                run_lock.release()

        def stream_run(self, inputs):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            connected = True
            try:
                self.end_headers()
            except OSError:
                connected = False

            events: list[dict[str, Any]] = []

            def send(message):
                nonlocal connected
                if connected:
                    if message.get("file"):
                        message = {**message, "file": relative_source(root, message["file"])}
                    data = json.dumps(message, allow_nan=False).encode() + b"\n"
                    try:
                        self.wfile.write(data)
                    except OSError:
                        connected = False

            def record_event(event):
                events.append(event)
                send(event)

            try:
                started = perf_counter()
                result = run(flow, inputs, record_event, include_metadata=True)
                try:
                    history.save(events, loaded_sources, (perf_counter() - started) * 1000)
                except OSError:
                    self.log_error("Could not save run history for %s.", result.id)
                    send({"history_error": "This run could not be saved to history."})
                send({"result": {"status": result.status, "output": result.output}})
            except (TypeError, ValueError) as error:
                send({"error": str(error)})

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"Workspace UI: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
