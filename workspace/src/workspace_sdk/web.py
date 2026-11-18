from collections import deque
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from inspect import getdoc
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock
from typing import Any

from .console import capture_console
from .dashboard import ASSET_ROUTE, PANELS_ROUTE, SETTINGS_ROUTE, dashboard_assets, dashboard_panels
from .flow import Flow, Output
from .history import RunHistory
from .node_sources import SourceChanged, edit_source_file, node_files, node_source_path
from .node_settings import LiveSettings, SettingsStore, parse_setting_defaults
from .project import ordered_steps, relative_source, setting_files, source_location
from .execution import FlowBusy, clear_external_snapshot, external_snapshot, run_recorded, sheet_busy, sheet_lock
from .ui_settings import ZoomSettings


STATIC = Path(__file__).with_name("static")
NODE_FILES_ROUTE = r"node-files/([0-9]{1,8})"
NODE_SOURCE_ROUTE = r"node-source/([0-9]{1,8})"
FLOW_VIEW_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/canvas-layout.js": ("canvas-layout.js", "text/javascript; charset=utf-8"),
    "/dashboard-ui.js": ("dashboard-ui.js", "text/javascript; charset=utf-8"),
    "/edge-routing.js": ("edge-routing.js", "text/javascript; charset=utf-8"),
    "/prism-syntax.js": ("prism-syntax.js", "text/javascript; charset=utf-8"),
}
FLOW_VIEW_SUBPATH_PATTERN = (
    r"(/(?:app\.css|app\.js|canvas-layout\.js|dashboard-ui\.js|edge-routing\.js|prism-syntax\.js|project|"
    r"dashboard-(?:panels|settings)/(?:workflow|[0-9]{1,8})|"
    r"dashboard-assets/(?:workflow|[0-9]{1,8})(?:/[A-Za-z0-9._-]+)?|"
    r"live-run|node-(?:files|source)/[0-9]{1,8}|history(?:/[0-9a-f]{32})?|run)?)"
)
_FLOW_VIEW_SUBPATH_RE = re.compile(FLOW_VIEW_SUBPATH_PATTERN)


def is_flow_view_route(method: str, subpath: str) -> bool:
    """Return True when subpath is an allowed GET or POST Flow View route."""
    if _FLOW_VIEW_SUBPATH_RE.fullmatch(subpath) is None:
        return False
    if method == "GET":
        return subpath != "/run" and not subpath.startswith("/node-source/")
    if method == "POST":
        return subpath == "/run" or subpath.startswith(("/node-source/", "/dashboard-settings/"))
    return False


def matches_ui_host(request: BaseHTTPRequestHandler) -> bool:
    # Check the accepted socket's destination, including when the listener binds all interfaces.
    ipv4, port = request.connection.getsockname()
    host = request.headers.get("Host")
    return host == f"{ipv4}:{port}" or (port == 80 and host == ipv4)


def ui_address(ipv4: str, port: int) -> str:
    browser_ip = "127.0.0.1" if ipv4 == "0.0.0.0" else ipv4
    return f"http://{browser_ip}:{port}"


class UIRequestHandler(BaseHTTPRequestHandler):
    """Shared HTTP transport helpers for the per-flow UI server and Workspace proxy."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def send_bytes(self, data: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(json.dumps(data).encode(), "application/json", status)

    send_source = send_json

    def local_request(self) -> bool:
        return matches_ui_host(self)

    def valid_post_origin(self) -> bool:
        origin = self.headers.get("Origin")
        return self.local_request() and origin in (None, f"http://{self.headers.get('Host')}")

    def read_json_dict(self, max_bytes: int = 1024 * 1024) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= max_bytes:
            raise ValueError
        payload = json.loads(self.rfile.read(size))
        if not isinstance(payload, dict):
            raise ValueError
        return payload

    def send_source_edit(self, edit_fn: Callable[[], dict[str, str]], *, not_found_error: str) -> None:
        try:
            result = edit_fn()
        except FileNotFoundError:
            self.send_json({"error": not_found_error}, HTTPStatus.NOT_FOUND)
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
    live_lock = Lock()
    live_events = {}
    console_events = deque(maxlen=128)
    live_sequence = 0

    class Handler(UIRequestHandler):
        def log_request(self, code="-", size="-"):
            if self.path != "/status":
                super().log_request(code, size)

        def scoped_steps(self, scope):
            return steps if scope == "workflow" else (steps[int(scope)],)

        def do_GET(self):
            if not self.local_request():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            asset = FLOW_VIEW_ASSETS.get(self.path)
            if asset:
                name, content_type = asset
                self.send_bytes((STATIC / name).read_bytes(), content_type)
            elif self.path == "/live-run":
                with live_lock:
                    data = {
                        "running": run_status == "running",
                        "events": sorted([*live_events.values(), *console_events], key=lambda item: item[0]),
                    }
                self.send_json((external_snapshot(root) if run_status != "running" else None) or data)
            elif self.path == "/project":
                data = json.dumps({
                    "name": root.name, "steps": graph, "input": default_input, "output": flow.output.step_id,
                    "zoom": zoom,
                }).encode()
                self.send_bytes(data, "application/json")
            elif self.path == "/status":
                self.send_bytes(json.dumps({"status": "running" if sheet_busy(root) else run_status}).encode(), "application/json")
            elif match := re.fullmatch(f"/{PANELS_ROUTE}", self.path):
                scope = match[1]
                try:
                    step_id = None if scope == "workflow" else self.scoped_steps(scope)[0].id
                    self.send_source({"panels": dashboard_panels(root, step_id)})
                except IndexError:
                    self.send_source({"error": "Dashboard scope not found."}, HTTPStatus.NOT_FOUND)
            elif match := re.fullmatch(f"/{ASSET_ROUTE}", self.path):
                scope = match[1]
                panel_id = match[2]
                try:
                    step_id = None if scope == "workflow" else self.scoped_steps(scope)[0].id
                    assets = dashboard_assets(root, step_id, panel_id)
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
            if not self.valid_post_origin():
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
                inputs = self.read_json_dict()
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
                with sheet_lock(root):
                    clear_external_snapshot(root)
                    run_status = "running"
                    self.stream_run(inputs)
            except FlowBusy as error:
                self.send_error(HTTPStatus.CONFLICT, str(error))
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
            if source is not None and (run_status == "running" or sheet_busy(root)):
                self.send_source({"error": "Cannot edit node files while the flow is running."}, HTTPStatus.CONFLICT)
                return

            def perform_edit() -> dict[str, str]:
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
                    return edit_source_file(path, source, revision, initial=initial)

            self.send_source_edit(perform_edit, not_found_error="This source file is unavailable in the node folder.")

        def stream_run(self, inputs):
            nonlocal run_status, live_sequence
            with live_lock:
                live_events.clear()
                console_events.clear()
            console_run_id = None
            event_lock = Lock()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            connected = True
            try:
                self.end_headers()
            except OSError:
                connected = False

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

            def remember(event):
                nonlocal live_sequence
                # Keep latest values per node/group rather than an unbounded stream.
                kind = event.get("status")
                for field in ("output_preview", "live_frame", "live_metrics", "setting_change", "result", "error"):
                    if field in event:
                        kind = field
                        if field == "live_metrics":
                            kind += ":" + next(iter(event[field]))
                        elif field == "setting_change":
                            kind += ":" + event[field]["name"]
                        break
                with live_lock:
                    live_sequence += 1
                    if event.get("kind") == "console":
                        console_events.append((live_sequence, event))
                        return
                    node = event.get("step_id")
                    live_events[node, kind] = (live_sequence, event)
                    if "live_metrics" in event:
                        groups = [key for key in live_events if key[0] == node and str(key[1]).startswith("live_metrics:")]
                        for key in groups[:-8]:
                            del live_events[key]
                    if node is None and event.get("status") in ("succeeded", "failed"):
                        for key in list(live_events):
                            if key[1] == "live_frame":
                                del live_events[key]

            def record_event(event):
                nonlocal console_run_id
                with event_lock:
                    if console_run_id is None:
                        console_run_id = event.get("run_id")
                    remember(event)
                    send(event)

            def record_console(stream, text):
                if console_run_id is not None:
                    record_event({"kind": "console", "run_id": console_run_id, "stream": stream, "text": text})

            try:
                with capture_console(record_console):
                    result = run_recorded(
                        flow, root, loaded_sources, record_event, inputs=inputs, live_metrics=True, live_frames=True,
                        live_settings=live_settings,
                    )
                completed = {"result": {"status": result.status, "output": result.output}}
                record_event(completed)
                run_status = result.status
            except (TypeError, ValueError) as error:
                record_event({"error": str(error)})
                run_status = "failed"
            except (OSError, sqlite3.Error):
                record_event({"error": "Could not read node settings."})
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
