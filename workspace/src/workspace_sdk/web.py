from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from inspect import getdoc
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock
from time import perf_counter
from typing import Any

from .dashboard import ASSET_ROUTE, SETTINGS_ROUTE, dashboard_assets
from .flow import Flow, Output
from .history import RunHistory
from .node_sources import SourceChanged, edit_source_file, node_files, node_source_path
from .node_settings import LiveSettings, SettingsStore, parse_setting_defaults
from .project import ordered_steps, relative_source, setting_files, source_location
from .runner import run
from .ui_settings import ZoomSettings


STATIC = Path(__file__).with_name("static")
NODE_FILES_ROUTE = r"node-files/([0-9]{1,8})"
NODE_SOURCE_ROUTE = r"node-source/([0-9]{1,8})"


def matches_ui_host(request: BaseHTTPRequestHandler) -> bool:
    # Check the accepted socket's destination, including when the listener binds all interfaces.
    ipv4, port = request.connection.getsockname()
    host = request.headers.get("Host")
    return host == f"{ipv4}:{port}" or (port == 80 and host == ipv4)


def ui_address(ipv4: str, port: int) -> str:
    browser_ip = "127.0.0.1" if ipv4 == "0.0.0.0" else ipv4
    return f"http://{browser_ip}:{port}"


def serve(
    flow: Flow, root: Path, port: int, loaded_sources: dict[Path, str],
    *, zoom: ZoomSettings, ipv4: str = "127.0.0.1", on_ready: Callable[[str], None] | None = None,
) -> None:
    graph = []
    steps = ordered_steps(flow)
    for step in steps:
        file, line = source_location(step.function)
        graph.append({
            "id": step.id,
            "file": file,
            "line": line,
            "source": relative_source(root, file),
            "description": (getdoc(step.function) or "").split("\n\n", 1)[0],
            "track_generator": step.track_generator,
            "dependencies": list(dict.fromkeys(
                ref.step_id for ref in step.arguments.values() if isinstance(ref, Output)
            )),
            "arguments": [
                {
                    "name": name,
                    "source": ref.step_id if isinstance(ref, Output) else ref.name,
                    "kind": "node" if isinstance(ref, Output) else "input",
                }
                for name, ref in step.arguments.items()
            ],
        })

    node_files_by_id = {step["id"]: step["file"] for step in graph}
    defaults_files = setting_files(root, steps)
    fixture = root / "sample.json"
    default_input = json.loads(fixture.read_text())["input"] if fixture.is_file() else {}
    history = RunHistory(root)
    settings = SettingsStore(root)
    live_settings = LiveSettings()
    run_lock = Lock()
    source_lock = Lock()
    run_status = "ready"

    class Handler(BaseHTTPRequestHandler):
        def log_request(self, code="-", size="-"):
            if self.path != "/status":
                super().log_request(code, size)

        def send_bytes(self, data, content_type, status=HTTPStatus.OK):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def send_source(self, data, status=HTTPStatus.OK):
            self.send_bytes(json.dumps(data).encode(), "application/json", status)

        def local_request(self):
            return matches_ui_host(self)

        def scoped_steps(self, scope):
            return steps if scope == "workflow" else (steps[int(scope)],)

        def do_GET(self):
            if not self.local_request():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            asset = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.css": ("app.css", "text/css; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/dashboard-ui.js": ("dashboard-ui.js", "text/javascript; charset=utf-8"),
                "/edge-routing.js": ("edge-routing.js", "text/javascript; charset=utf-8"),
                "/prism-syntax.js": ("prism-syntax.js", "text/javascript; charset=utf-8"),
            }.get(self.path)
            if asset:
                name, content_type = asset
                self.send_bytes((STATIC / name).read_bytes(), content_type)
            elif self.path == "/project":
                data = json.dumps({
                    "name": root.name, "steps": graph, "input": default_input, "output": flow.output.step_id,
                    "zoom": zoom,
                }).encode()
                self.send_bytes(data, "application/json")
            elif self.path == "/status":
                self.send_bytes(json.dumps({"status": run_status}).encode(), "application/json")
            elif match := re.fullmatch(f"/{ASSET_ROUTE}", self.path):
                scope = match[1]
                try:
                    step_id = None if scope == "workflow" else self.scoped_steps(scope)[0].id
                    assets = dashboard_assets(root, step_id)
                except (FileNotFoundError, IndexError):
                    self.send_source({"error": "Add index.html to this scope's dashboard folder."}, HTTPStatus.NOT_FOUND)
                except PermissionError:
                    self.send_source({"error": "Load dashboard assets from files inside the dashboard folder."}, HTTPStatus.FORBIDDEN)
                except (OSError, RuntimeError, ValueError):
                    self.send_source({"error": "Dashboard assets must be UTF-8 files up to 256 KiB each."}, HTTPStatus.BAD_REQUEST)
                else:
                    self.send_source(assets)
            elif match := re.fullmatch(f"/{SETTINGS_ROUTE}", self.path):
                try:
                    scoped = self.scoped_steps(match[1])
                    current = settings.snapshot(scoped)
                except IndexError:
                    self.send_error(HTTPStatus.NOT_FOUND)
                except (OSError, sqlite3.Error):
                    self.send_source({"error": "Could not read node settings."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                else:
                    self.send_source({
                        step.id: {
                            name: {
                                "type": type(setting.default).__name__,
                                "value": current[step.id][name],
                                "minimum": setting.minimum,
                                "maximum": setting.maximum,
                                "live": setting.live,
                            }
                            for name, setting in step.settings.items()
                        }
                        for step in scoped if step.settings
                    })
            elif match := re.fullmatch(f"/{NODE_FILES_ROUTE}", self.path):
                index = int(match[1])
                if index >= len(graph):
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                try:
                    data = node_files(root, graph[index]["file"])
                except (OSError, RuntimeError):
                    self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not read the node folder.")
                    return
                self.send_bytes(json.dumps(data).encode(), "application/json")
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
            nonlocal run_status
            origin = self.headers.get("Origin")
            if not self.local_request() or origin not in (None, f"http://{self.headers.get('Host')}"):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            source_route = re.fullmatch(f"/{NODE_SOURCE_ROUTE}", self.path)
            settings_route = re.fullmatch(f"/{SETTINGS_ROUTE}", self.path)
            if self.path != "/run" and source_route is None and settings_route is None:
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
            if source_route is not None:
                self.edit_source(int(source_route[1]), inputs)
                return
            if settings_route is not None:
                self.change_setting(settings_route[1], inputs)
                return

            if not run_lock.acquire(blocking=False):
                self.send_error(HTTPStatus.CONFLICT, "A run is already in progress.")
                return

            try:
                run_status = "running"
                self.stream_run(inputs)
            finally:
                if run_status == "running":
                    run_status = "failed"
                run_lock.release()

        def change_setting(self, scope, data):
            try:
                node, name, mode = data.get("nodeId"), data.get("name"), data.get("mode")
                if not isinstance(node, str) or not isinstance(name, str):
                    raise ValueError("Choose an editable node setting.")
                setting = next((step.settings.get(name) for step in self.scoped_steps(scope) if step.id == node), None)
                if setting is None:
                    raise ValueError("This setting is unavailable in the dashboard scope.")
                value = setting.validate(data.get("value"))
                if mode == "next":
                    settings.save(node, name, value)
                    result = {"status": "saved", "name": name, "value": value}
                elif mode == "live":
                    run_id = data.get("runId")
                    if not setting.live or not isinstance(run_id, str):
                        raise ValueError("This setting cannot change in the selected run.")
                    result = live_settings.queue(run_id, node, name, value)
                else:
                    raise ValueError("Choose the next run or a live run.")
            except (IndexError, ValueError) as error:
                self.send_source({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except (OSError, sqlite3.Error):
                self.send_source({"error": "Could not save this setting."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self.send_source(result)

        def edit_source(self, index: int, data: dict):
            relative = data.get("path")
            source = data.get("source")
            revision = data.get("revision")
            if not isinstance(relative, str) or (
                "source" in data and (not isinstance(source, str) or not isinstance(revision, str))
            ):
                self.send_source({"error": "Send a file path and, to save, the source and revision."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                if index >= len(graph):
                    raise FileNotFoundError
                with source_lock:
                    path = node_source_path(root, graph[index]["file"], relative)
                    definitions = defaults_files.get(path)
                    initial = None
                    if definitions is not None:
                        if source is not None:
                            parse_setting_defaults(source, definitions)
                        initial = json.dumps({
                            name: declarations[0].default
                            for name, declarations in definitions.items()
                            if all(
                                type(setting.default) is type(declarations[0].default)
                                and setting.default == declarations[0].default for setting in declarations
                            )
                        }, indent=2) + "\n"
                    result = edit_source_file(path, source, revision, initial=initial)
            except FileNotFoundError:
                self.send_source({"error": "This source file is unavailable in the node folder."}, HTTPStatus.NOT_FOUND)
            except SourceChanged:
                self.send_source({"error": "This file changed on disk. Reopen it before saving."}, HTTPStatus.CONFLICT)
            except (UnicodeError, SyntaxError):
                self.send_source({"error": "Could not read or write the file in its original encoding."}, HTTPStatus.BAD_REQUEST)
            except ValueError as error:
                self.send_source({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except (OSError, RuntimeError):
                self.send_source({"error": "Could not read or write the file."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self.send_source(result)

        def stream_run(self, inputs):
            nonlocal run_status
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
                        file = relative_source(root, message["file"])
                        message = {**message, "file": file}
                        if (message["status"] == "failed" and not message.get("blocked_by")
                                and file is not None and message.get("step_id") in node_files_by_id):
                            try:
                                path = node_source_path(root, node_files_by_id[message["step_id"]], file)
                            except (OSError, RuntimeError):
                                pass
                            else:
                                message["source_target"] = path.relative_to(root).as_posix()
                    data = json.dumps(message, allow_nan=False).encode() + b"\n"
                    try:
                        self.wfile.write(data)
                    except OSError:
                        connected = False

            def record_event(event):
                if not event.keys() & {"output_preview", "live_frame", "live_metrics", "setting_change"}:
                    events.append(event)
                send(event)

            try:
                started = perf_counter()
                result = run(
                    flow, inputs, record_event, include_metadata=True, live_metrics=True, live_frames=True,
                    settings=settings.snapshot(steps), live_settings=live_settings,
                )
                try:
                    history.save(events, loaded_sources, (perf_counter() - started) * 1000)
                except OSError:
                    self.log_error("Could not save run history for %s.", result.id)
                    send({"history_error": "This run could not be saved to history."})
                send({"result": {"status": result.status, "output": result.output}})
                run_status = result.status
            except (TypeError, ValueError) as error:
                send({"error": str(error)})
                run_status = "failed"
            except (OSError, sqlite3.Error):
                send({"error": "Could not read node settings."})
                run_status = "failed"

    with ThreadingHTTPServer((ipv4, port), Handler) as server:
        address = ui_address(ipv4, server.server_port)
        if on_ready is None:
            print(f"Workspace UI: {address}", flush=True)
        else:
            on_ready(address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
