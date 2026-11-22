from collections import deque
from collections.abc import Callable
from functools import lru_cache
import gzip
from hashlib import blake2b
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from inspect import getdoc
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .console import ConsoleLogStore, capture_console
from .dashboard import (
    ASSET_ROUTE, DASHBOARD_BRIDGE_VERSION, MAX_WORKSPACE_FRAME_SOURCES, PANELS_ROUTE, SETTINGS_ROUTE,
    dashboard_assets, dashboard_panels,
)
from .dashboard_editor import SOURCE_ROUTE as DASHBOARD_SOURCE_ROUTE, send_dashboard_edit
from .flow import Flow, Output
from .history import RunHistory
from .node_sources import SourceChanged, edit_source_file, node_files, node_source_path
from .node_settings import LiveSettings, SettingsStore, parse_setting_defaults
from .project import ordered_steps, relative_source, setting_files, source_location
from .execution import FlowBusy, clear_external_snapshot, external_snapshot, run_recorded, sheet_busy, sheet_lock
from .sheet_locks import SheetEditLease, SheetLockedError
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
    "/dashboard-bridge.js": ("dashboard-bridge.js", "text/javascript; charset=utf-8"),
    "/dashboard-editor.js": ("dashboard-editor.js", "text/javascript; charset=utf-8"),
    "/edge-routing.js": ("edge-routing.js", "text/javascript; charset=utf-8"),
    "/live-sync.js": ("live-sync.js", "text/javascript; charset=utf-8"),
    "/prism-syntax.js": ("prism-syntax.js", "text/javascript; charset=utf-8"),
    **{
        f"/flow-view/{name}.js": (f"flow-view/{name}.js", "text/javascript; charset=utf-8")
        for name in ("display", "graph", "inspector", "node-source", "run-controls")
    },
}
FLOW_VIEW_ASSET_PATTERN = "|".join(re.escape(path) for path in FLOW_VIEW_ASSETS if path != "/")
FLOW_VIEW_SUBPATH_PATTERN = (
    rf"({FLOW_VIEW_ASSET_PATTERN}|/(?:project|"
    rf"dashboard-(?:panels|settings)/(?:workflow|[0-9]{{1,8}})|{DASHBOARD_SOURCE_ROUTE}|"
    r"dashboard-assets/(?:workflow|[0-9]{1,8})(?:/[A-Za-z0-9._-]+)?|"
    r"live-run(?:\?[A-Za-z0-9_=&-]+)?|node-(?:files|source)/[0-9]{1,8}|history(?:/[0-9a-f]{32})?|console-logs(?:/[0-9a-f]{32})?|run)?)"
)
_FLOW_VIEW_SUBPATH_RE = re.compile(FLOW_VIEW_SUBPATH_PATTERN)
GZIP_MIN_BYTES = 512
COMPRESSIBLE_PREFIXES = ("text/", "application/json")


def _normalize_etag(tag: str) -> str:
    value = tag.strip()
    if value.startswith("W/"):
        value = value[2:].strip()
    return value


def _etag_matches(header_value: str | None, etag: str) -> bool:
    if not header_value:
        return False
    target = _normalize_etag(etag)
    for part in header_value.split(","):
        token = part.strip()
        if token == "*" or _normalize_etag(token) == target:
            return True
    return False


def _content_etag(data: bytes) -> str:
    return f'W/"{blake2b(data, digest_size=12).hexdigest()}"'


@lru_cache(maxsize=64)
def _cached_static_asset(path: Path, mtime_ns: int, size: int) -> tuple[bytes, bytes | None, str]:
    raw = path.read_bytes()
    etag = _content_etag(raw)
    compressed = gzip.compress(raw, compresslevel=5) if len(raw) >= GZIP_MIN_BYTES else None
    if compressed is not None and len(compressed) >= len(raw):
        compressed = None
    return raw, compressed, etag


def is_flow_view_route(method: str, subpath: str) -> bool:
    """Return True when subpath is an allowed GET or POST Flow View route."""
    if _FLOW_VIEW_SUBPATH_RE.fullmatch(subpath) is None:
        return False
    if method == "GET":
        return subpath != "/run" and not subpath.startswith(("/node-source/", "/dashboard-source/"))
    if method == "POST":
        return (
            subpath == "/run"
            or subpath == "/console-logs"
            or subpath.startswith(("/node-source/", "/dashboard-settings/", "/dashboard-source/"))
        )
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
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def accepts_gzip(self) -> bool:
        header = self.headers.get("Accept-Encoding", "")
        for part in header.split(","):
            token = part.split(";", 1)[0].strip().lower()
            if token == "gzip":
                return True
        return False

    def _merge_vary(self, vary: str | None, *, include_encoding: bool) -> str | None:
        parts = []
        if vary:
            parts.extend(item.strip() for item in vary.split(",") if item.strip())
        if include_encoding and not any(item.lower() == "accept-encoding" for item in parts):
            parts.append("Accept-Encoding")
        return ", ".join(parts) if parts else None

    def send_not_modified(self, etag: str, *, cache_control: str = "no-cache", vary: str | None = None) -> None:
        self.send_response(HTTPStatus.NOT_MODIFIED)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache_control)
        merged_vary = self._merge_vary(vary, include_encoding=True)
        if merged_vary:
            self.send_header("Vary", merged_vary)
        self.end_headers()

    def send_bytes(
        self,
        data: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        etag: str | None = None,
        cache_control: str = "no-store",
        compress: bool = True,
        vary: str | None = None,
    ) -> None:
        if status == HTTPStatus.OK and etag is not None and _etag_matches(self.headers.get("If-None-Match"), etag):
            self.send_not_modified(etag, cache_control=cache_control, vary=vary)
            return
        compressible = compress and content_type.startswith(COMPRESSIBLE_PREFIXES)
        payload = data
        encoded = False
        if compressible and self.accepts_gzip() and len(data) >= GZIP_MIN_BYTES:
            candidate = gzip.compress(data, compresslevel=5)
            if len(candidate) < len(data):
                payload = candidate
                encoded = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache_control)
        if etag is not None:
            self.send_header("ETag", etag)
        if encoded:
            self.send_header("Content-Encoding", "gzip")
        merged_vary = self._merge_vary(vary, include_encoding=compressible)
        if merged_vary:
            self.send_header("Vary", merged_vary)
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, data: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(json.dumps(data).encode(), "application/json", status)

    def send_conditional_json(
        self,
        data: object,
        *,
        etag: str | None = None,
        status: HTTPStatus = HTTPStatus.OK,
        vary: str | None = None,
    ) -> None:
        raw = json.dumps(data).encode()
        resolved_etag = etag if etag is not None else _content_etag(raw)
        self.send_bytes(
            raw,
            "application/json",
            status,
            etag=resolved_etag,
            cache_control="no-cache",
            compress=True,
            vary=vary,
        )

    def send_static_asset(self, path: Path, content_type: str) -> None:
        info = path.stat()
        raw, compressed, etag = _cached_static_asset(path, info.st_mtime_ns, info.st_size)
        if _etag_matches(self.headers.get("If-None-Match"), etag):
            self.send_not_modified(etag, cache_control="no-cache")
            return
        use_gzip = compressed is not None and self.accepts_gzip()
        payload = compressed if use_gzip else raw
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", etag)
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        self.wfile.write(payload)

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
        except SheetLockedError as error:
            self.send_json({"error": str(error), "edit_lock": error.snapshot}, HTTPStatus.CONFLICT)
        except FlowBusy as error:
            self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
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


class LiveRunBuffer:
    def __init__(self, root: Path, console_logs: ConsoleLogStore) -> None:
        self._root = root
        self._console_logs = console_logs
        self._lock = Lock()
        self._run_status = "ready"
        self._current_run_id: str | None = None
        self._live_events: dict[tuple[Any, Any], tuple[int, dict]] = {}
        self._console_events: deque[tuple[int, dict]] = deque(maxlen=128)
        self._sequence = 0
        self._state_version = 0
        self._saved_fp: str | None = None
        self._saved_events: list[dict[str, str]] = []

    def status(self) -> str:
        with self._lock:
            return self._run_status

    def is_running(self) -> bool:
        with self._lock:
            return self._run_status == "running"

    def begin_run(self, run_id: str | None) -> None:
        with self._lock:
            self._live_events.clear()
            self._console_events.clear()
            self._current_run_id = run_id
            self._run_status = "running"
            self._state_version += 1

    def finish_run(self, status: str) -> None:
        with self._lock:
            self._run_status = status
            self._state_version += 1

    def mark_failed_if_running(self) -> None:
        with self._lock:
            if self._run_status == "running":
                self._run_status = "failed"
                self._state_version += 1

    def record(self, event: dict) -> None:
        kind = event.get("status")
        for field in ("output_preview", "live_frame", "live_metrics", "setting_change", "result", "error"):
            if field in event:
                kind = field
                if field == "live_metrics":
                    kind += ":" + next(iter(event[field]))
                elif field == "setting_change":
                    kind += ":" + event[field]["name"]
                break
        with self._lock:
            self._sequence += 1
            if event.get("run_id"):
                self._current_run_id = event["run_id"]
            if event.get("kind") == "console":
                self._console_events.append((self._sequence, event))
                return
            node = event.get("step_id")
            self._live_events[node, kind] = (self._sequence, event)
            if "live_metrics" in event:
                groups = [key for key in self._live_events if key[0] == node and str(key[1]).startswith("live_metrics:")]
                for key in groups[:-8]:
                    del self._live_events[key]
            if node is None and event.get("status") in ("succeeded", "failed"):
                for key in list(self._live_events):
                    if key[1] == "live_frame":
                        del self._live_events[key]

    def dashboard_live(self) -> dict | None:
        with self._lock:
            started = self._live_events.get((None, "running")) if self._run_status == "running" else None
            if started is None:
                return None
            run = started[1]
            metrics, metric_times = {}, {}
            for (step, _), (_, event) in self._live_events.items():
                if event.get("run_id") == run["run_id"] and "live_metrics" in event:
                    metrics.setdefault(step, {}).update(event["live_metrics"])
                    metric_times.setdefault(step, {}).update(dict.fromkeys(event["live_metrics"], event["time"]))
            return {
                "id": run["run_id"],
                "status": "running",
                "started_at": run["time"],
                "metrics": metrics,
                "metric_times": metric_times,
            }

    def _saved_console_fingerprint(self) -> str:
        try:
            today = self._console_logs.current_date().isoformat()
        except (OSError, ValueError):
            return f"err:{self._sequence}"
        try:
            settings_info = self._console_logs.settings_path.stat()
            settings_part = f"{settings_info.st_mtime_ns}:{settings_info.st_size}"
        except OSError:
            settings_part = "default"
        logs_dir = self._console_logs.logs_dir
        try:
            if logs_dir.is_symlink():
                return f"sym:{today}:{settings_part}"
            dir_info = logs_dir.stat()
            entries = []
            for run_dir in sorted(logs_dir.iterdir(), key=lambda path: path.name):
                if run_dir.is_symlink() or not run_dir.is_dir():
                    continue
                run_info = run_dir.stat()
                segments = []
                for segment in sorted(run_dir.iterdir(), key=lambda path: path.name):
                    if segment.is_symlink() or not segment.is_file():
                        continue
                    seg_info = segment.stat()
                    segments.append(f"{segment.name}:{seg_info.st_mtime_ns}:{seg_info.st_size}")
                if segments:
                    entries.append(f"{run_dir.name}:{run_info.st_mtime_ns}:{','.join(segments)}")
            return f"logs:{today}:{settings_part}:{dir_info.st_mtime_ns}:{'|'.join(entries)}"
        except FileNotFoundError:
            return f"empty:{today}:{settings_part}"
        except OSError:
            return f"err:{today}:{settings_part}:{self._sequence}"

    def _latest_saved_events(self) -> tuple[str, list[dict[str, str]]]:
        fp = self._saved_console_fingerprint()
        with self._lock:
            if fp == self._saved_fp:
                return fp, self._saved_events
        try:
            saved = self._console_logs.latest_events()
        except (OSError, ValueError):
            saved = []
        with self._lock:
            self._saved_fp = fp
            self._saved_events = saved
        return fp, saved

    def live_response(
        self,
        if_none_match: str | None,
        after: int | None,
        client_run_id: str | None,
    ) -> tuple[str, dict | None]:
        if not self.is_running():
            external = external_snapshot(self._root)
            if external is not None:
                events = external.get("events", [])
                ext_run_id = next(
                    (
                        item[1].get("run_id")
                        for item in events
                        if isinstance(item, list) and len(item) == 2 and isinstance(item[1], dict) and item[1].get("run_id")
                    ),
                    None,
                ) or external.get("job_id") or ""
                max_seq = max(
                    (item[0] for item in events if isinstance(item, list) and len(item) == 2 and isinstance(item[0], int)),
                    default=0,
                )
                etag = f'W/"ext:{ext_run_id}:{int(bool(external.get("running")))}:{external.get("status", "")}:{max_seq}:{len(events)}"'
                if _etag_matches(if_none_match, etag):
                    return etag, None
                if after is not None and client_run_id and client_run_id == ext_run_id:
                    delta = [item for item in events if isinstance(item, list) and len(item) == 2 and item[0] > after]
                    return etag, {
                        **external,
                        "events": delta,
                        "incremental": True,
                        "cursor": max_seq,
                        "run_id": ext_run_id,
                    }
                if after is not None:
                    return etag, {
                        **external,
                        "incremental": False,
                        "cursor": max_seq,
                        "run_id": ext_run_id,
                    }
                return etag, external

        with self._lock:
            has_memory = self._run_status == "running" or bool(self._live_events) or bool(self._console_events)
            if has_memory:
                run_id = self._current_run_id
                running = self._run_status == "running"
                seq = self._sequence
                etag = f'W/"mem:{run_id or ""}:{int(running)}:{self._run_status}:{self._state_version}:{seq}"'
                if _etag_matches(if_none_match, etag):
                    return etag, None
                if after is not None and client_run_id is not None and client_run_id == run_id:
                    events = sorted(
                        (item for item in (*self._live_events.values(), *self._console_events) if item[0] > after),
                        key=lambda item: item[0],
                    )
                    return etag, {
                        "running": running,
                        "run_id": run_id,
                        "cursor": seq,
                        "incremental": True,
                        "events": events,
                    }
                events = sorted([*self._live_events.values(), *self._console_events], key=lambda item: item[0])
                payload: dict[str, Any] = {"running": running, "events": events}
                if after is not None or run_id is not None:
                    payload["run_id"] = run_id
                    payload["cursor"] = seq
                    payload["incremental"] = False
                return etag, payload

        fp, saved = self._latest_saved_events()
        saved_run_id = saved[0]["run_id"] if saved else None
        max_seq = len(saved)
        etag = f'W/"saved:{blake2b(fp.encode(), digest_size=8).hexdigest()}:{saved_run_id or ""}:{max_seq}"'
        if _etag_matches(if_none_match, etag):
            return etag, None
        indexed = [(index, event) for index, event in enumerate(saved, start=1)]
        if after is not None and client_run_id is not None and client_run_id == saved_run_id:
            delta = [item for item in indexed if item[0] > after]
            return etag, {
                "running": False,
                "run_id": saved_run_id,
                "cursor": max_seq,
                "incremental": True,
                "events": delta,
            }
        payload = {"running": False, "events": indexed}
        if after is not None or saved_run_id is not None:
            payload["run_id"] = saved_run_id
            payload["cursor"] = max_seq
            payload["incremental"] = False
        return etag, payload


def _parse_live_run_query(query: str) -> tuple[int | None, str | None]:
    if not query:
        return None, None
    params = parse_qs(query, keep_blank_values=False)
    if not params.keys() <= {"after", "run_id"}:
        raise ValueError("Unsupported live-run query parameters.")
    after = None
    if "after" in params:
        raw = params["after"][-1]
        if not raw.isdigit():
            raise ValueError("Invalid live-run cursor.")
        after = int(raw)
    client_run_id = params["run_id"][-1] if "run_id" in params else None
    return after, client_run_id


def sample_input(root: Path) -> dict:
    fixture = root / "sample.json"
    if not fixture.is_file():
        return {}
    sample = json.loads(fixture.read_text(encoding="utf-8"))
    if not isinstance(sample, dict) or not isinstance(sample.get("input"), dict):
        raise ValueError("sample.json must contain an input object.")
    return sample["input"]


def serve(
    flow: Flow, root: Path, port: int, loaded_sources: dict[Path, str],
    *, zoom: ZoomSettings, ipv4: str = "127.0.0.1", on_ready: Callable[[str, list[dict]], None] | None = None,
    run_token: str | None = None, workspace_managed: bool = False,
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
    default_input = sample_input(root)
    history = RunHistory(root)
    console_logs = ConsoleLogStore(root)
    settings = SettingsStore(root)
    live_settings = LiveSettings()
    run_lock = Lock()
    source_lock = Lock()
    live_buffer = LiveRunBuffer(root, console_logs)

    class Handler(UIRequestHandler):
        def log_request(self, code="-", size="-"):
            if self.path != "/status":
                super().log_request(code, size)

        def scoped_steps(self, scope):
            return steps if scope == "workflow" else (steps[int(scope)],)

        def dashboard_live_frames(self, query: str) -> None:
            try:
                parameters = parse_qs(query, strict_parsing=True, max_num_fields=MAX_WORKSPACE_FRAME_SOURCES)
                frame_steps = parameters["step"]
                if parameters.keys() != {"step"} or any(len(step) > 512 for step in frame_steps):
                    raise ValueError
            except (KeyError, ValueError):
                self.send_json({"error": "Invalid live dashboard frame selection."}, HTTPStatus.BAD_REQUEST)
                return
            data = None
            with live_buffer._lock:
                started = live_buffer._live_events.get((None, "running")) if live_buffer._run_status == "running" else None
                if started is not None:
                    run = started[1]
                    frames = {}
                    for step in frame_steps:
                        entry = live_buffer._live_events.get((step, "live_frame"))
                        if entry is not None and entry[1]["run_id"] == run["run_id"]:
                            event = entry[1]
                            frames[step] = {"jpeg": event["live_frame"], "time": event["time"]}
                    data = {"id": run["run_id"], "started_at": run["time"], "frames": frames}
            self.send_json(data)

        def dashboard_live_placement(self, query: str) -> None:
            try:
                parameters = parse_qs(query, strict_parsing=True, max_num_fields=1)
                node_id = parameters["node"][0] if query else None
            except (KeyError, ValueError):
                self.send_json({"error": "Invalid dashboard node selection."}, HTTPStatus.BAD_REQUEST)
                return
            data = None
            with live_buffer._lock:
                started = live_buffer._live_events.get((None, "running")) if live_buffer._run_status == "running" else None
                if started is not None and not any((None, status) in live_buffer._live_events for status in ("succeeded", "failed")):
                    run = started[1]
                    run_id = run["run_id"]
                    nodes, frames, metrics = {}, {}, {}
                    for (step, kind), (_, event) in live_buffer._live_events.items():
                        if step is None or (node_id is not None and step != node_id) or event.get("run_id") != run_id:
                            continue
                        if kind == "live_frame":
                            frames[step] = f"data:image/jpeg;base64,{event['live_frame']}"
                        elif "live_metrics" in event:
                            metrics.setdefault(step, {}).update(event["live_metrics"])
                        elif kind in ("running", "succeeded", "failed", "stopped", "skipped"):
                            entry = nodes.setdefault(step, {"id": step})
                            entry["status"] = kind
                            if kind == "running":
                                entry["started_at"] = event["time"]
                            elif kind != "skipped":
                                entry["finished_at"] = event["time"]
                            entry.update({field: event[field] for field in ("duration_ms", "metrics") if field in event})
                    data = {
                        "run": {"id": run_id, "status": "running", "started_at": run["time"],
                                "steps": list(nodes.values())},
                        "liveFrames": frames,
                        "liveMetrics": metrics,
                    }
            self.send_json(data)

        def do_GET(self):
            if not self.local_request():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            parsed = urlsplit(self.path)
            asset = FLOW_VIEW_ASSETS.get(self.path)
            if asset:
                name, content_type = asset
                self.send_static_asset(STATIC / name, content_type)
            elif parsed.path == "/live-run":
                try:
                    after, client_run_id = _parse_live_run_query(parsed.query)
                except ValueError as error:
                    self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                    return
                etag, data = live_buffer.live_response(
                    self.headers.get("If-None-Match"),
                    after,
                    client_run_id,
                )
                if data is None:
                    self.send_not_modified(etag)
                    return
                self.send_conditional_json(data, etag=etag)
            elif self.path == "/project":
                edit_lock = SheetEditLease(root).status(self.headers.get("X-Phyzflow-Edit-Session"))
                self.send_json({
                    "name": root.name, "steps": graph, "input": default_input, "output": flow.output.step_id,
                    "zoom": zoom, "edit_running_source": run_token is not None, "edit_lock": edit_lock,
                })
            elif self.path == "/status":
                self.send_json({"status": "running" if sheet_busy(root) else live_buffer.status()})
            elif self.path == "/dashboard-live":
                self.send_json(live_buffer.dashboard_live())
            elif self.path.partition("?")[0] == "/dashboard-live-placement":
                self.dashboard_live_placement(self.path.partition("?")[2])
            elif self.path.startswith("/dashboard-live-frames?"):
                self.dashboard_live_frames(self.path.partition("?")[2])
            elif match := re.fullmatch(f"/{PANELS_ROUTE}", self.path):
                scope = match[1]
                try:
                    step_id = None if scope == "workflow" else self.scoped_steps(scope)[0].id
                    self.send_json({"panels": dashboard_panels(root, step_id)})
                except IndexError:
                    self.send_json({"error": "Dashboard scope not found."}, HTTPStatus.NOT_FOUND)
            elif match := re.fullmatch(f"/{ASSET_ROUTE}", self.path):
                scope = match[1]
                panel_id = match[2]
                try:
                    step_id = None if scope == "workflow" else self.scoped_steps(scope)[0].id
                    assets = dashboard_assets(root, step_id, panel_id)
                except (FileNotFoundError, IndexError):
                    self.send_json({"error": "Add index.html to this scope's dashboard folder."}, HTTPStatus.NOT_FOUND)
                except PermissionError:
                    self.send_json({"error": "Load dashboard assets from files inside the dashboard folder."}, HTTPStatus.FORBIDDEN)
                except (OSError, RuntimeError, ValueError):
                    self.send_json({"error": "Dashboard assets must be UTF-8 files up to 256 KiB each."}, HTTPStatus.BAD_REQUEST)
                else:
                    self.send_json({"bridge_version": DASHBOARD_BRIDGE_VERSION, **assets})
            elif match := re.fullmatch(f"/{SETTINGS_ROUTE}", self.path):
                try:
                    scoped = self.scoped_steps(match[1])
                    current = settings.snapshot(scoped)
                except IndexError:
                    self.send_error(HTTPStatus.NOT_FOUND)
                except (OSError, sqlite3.Error):
                    self.send_json({"error": "Could not read node settings."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                else:
                    self.send_json({
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
                self.send_json(data)
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
                    self.send_json(data)
            elif self.path == "/console-logs" or self.path.startswith("/console-logs/"):
                run_id = None if self.path == "/console-logs" else self.path.removeprefix("/console-logs/")
                try:
                    current_settings = console_logs.settings()
                    events = console_logs.read_run(run_id)
                except ValueError as error:
                    self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                except OSError:
                    self.send_json({"error": "Could not read console logs."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                else:
                    resolved = run_id if run_id is not None else (events[0]["run_id"] if events else None)
                    self.send_json({"settings": current_settings, "run_id": resolved, "events": events})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self):
            if not self.valid_post_origin():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            source_route = re.fullmatch(f"/{NODE_SOURCE_ROUTE}", self.path)
            settings_route = re.fullmatch(f"/{SETTINGS_ROUTE}", self.path)
            dashboard_route = re.fullmatch(f"/{DASHBOARD_SOURCE_ROUTE}", self.path) if not workspace_managed else None
            if self.path not in ("/run", "/console-logs") and not any((source_route, settings_route, dashboard_route)):
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
            if dashboard_route is not None:
                scope = dashboard_route[1]
                try:
                    step_id = None if scope == "workflow" else self.scoped_steps(scope)[0].id
                except IndexError:
                    self.send_json({"error": "Dashboard scope not found."}, HTTPStatus.NOT_FOUND)
                else:
                    send_dashboard_edit(self, root, step_id, inputs)
                return
            if source_route is not None:
                self.edit_source(int(source_route[1]), inputs)
                return
            if settings_route is not None:
                self.change_setting(settings_route[1], inputs)
                return
            if self.path == "/console-logs":
                self.update_console_logs(inputs)
                return

            run_id = None
            if run_token is not None:
                run_id = self.headers.get("X-Phyzflow-Run-Id", "")
                if self.headers.get("X-Phyzflow-Worker-Token") != run_token or not re.fullmatch(r"[0-9a-f]{32}", run_id):
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return

            if not run_lock.acquire(blocking=False):
                self.send_error(HTTPStatus.CONFLICT, "A run is already in progress.")
                return

            try:
                with sheet_lock(root):
                    clear_external_snapshot(root)
                    self.stream_run(inputs, run_id)
            except FlowBusy as error:
                self.send_error(HTTPStatus.CONFLICT, str(error))
            finally:
                live_buffer.mark_failed_if_running()
                run_lock.release()

        def update_console_logs(self, data):
            try:
                updated = console_logs.update_settings(data)
            except ValueError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except OSError:
                self.send_json({"error": "Could not save console log settings."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self.send_json({"settings": updated})

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
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except (OSError, sqlite3.Error):
                self.send_json({"error": "Could not save this setting."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self.send_json(result)

        def edit_source(self, index: int, data: dict):
            relative = data.get("path")
            source = data.get("source")
            revision = data.get("revision")
            if not isinstance(relative, str) or (
                "source" in data and (not isinstance(source, str) or not isinstance(revision, str))
            ):
                self.send_json({"error": "Send a file path and, to save, the source and revision."}, HTTPStatus.BAD_REQUEST)
                return
            edit_while_running = run_token is not None and Path(relative).suffix == ".py"
            if source is not None and not edit_while_running and (live_buffer.is_running() or sheet_busy(root)):
                self.send_json({"error": "Cannot edit node files while the flow is running."}, HTTPStatus.CONFLICT)
                return

            def perform_edit() -> dict[str, str]:
                if index >= len(graph):
                    raise FileNotFoundError
                with source_lock:
                    path = node_source_path(root, graph[index]["file"], relative)
                    definitions = defaults_files.get(path)

                    def build_initial() -> str | None:
                        if definitions is None:
                            return None
                        return json.dumps({
                            name: declarations[0].default
                            for name, declarations in definitions.items()
                            if all(
                                type(setting.default) is type(declarations[0].default)
                                and setting.default == declarations[0].default for setting in declarations
                            )
                        }, indent=2) + "\n"

                    if source is None:
                        return edit_source_file(path, None, None, initial=build_initial())

                    def apply_node_edit() -> dict[str, str]:
                        if definitions is not None:
                            parse_setting_defaults(source, definitions)
                        return edit_source_file(path, source, revision, initial=build_initial())

                    return SheetEditLease(root).write_locked(
                        "web",
                        self.headers.get("X-Phyzflow-Edit-Session"),
                        apply_node_edit,
                        path=relative,
                    )

            self.send_source_edit(perform_edit, not_found_error="This source file is unavailable in the node folder.")

        def stream_run(self, inputs, run_id):
            live_buffer.begin_run(run_id)
            console_run_id = run_id
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

            def record_event(event):
                nonlocal console_run_id
                if run_id is not None and "run_id" not in event:
                    event = {**event, "run_id": run_id}
                with event_lock:
                    if console_run_id is None:
                        console_run_id = event.get("run_id")
                    live_buffer.record(event)
                    send(event)

            log_error_reported = False

            def report_log_error():
                nonlocal log_error_reported
                if not log_error_reported:
                    log_error_reported = True
                    record_event({"console_log_error": "Console logs could not be saved."})

            def record_console(stream, text):
                if console_run_id is not None:
                    try:
                        console_logs.append(console_run_id, stream, text)
                    except (OSError, ValueError):
                        report_log_error()
                    record_event({"kind": "console", "run_id": console_run_id, "stream": stream, "text": text})

            def tick_console():
                try:
                    console_logs.purge_expired()
                except (OSError, ValueError):
                    report_log_error()

            try:
                with capture_console(record_console, on_tick=tick_console):
                    result = run_recorded(
                        flow, root, loaded_sources, record_event, inputs=inputs, live_metrics=True, live_frames=True,
                        live_settings=live_settings, run_id=run_id,
                    )
                completed = {"result": {"status": result.status, "output": result.output}}
                record_event(completed)
                live_buffer.finish_run(result.status)
            except (TypeError, ValueError) as error:
                record_event({"error": str(error)})
                live_buffer.finish_run("failed")
            except (OSError, sqlite3.Error):
                record_event({"error": "Could not read node settings."})
                live_buffer.finish_run("failed")

    with ThreadingHTTPServer((ipv4, port), Handler) as server:
        address = ui_address(ipv4, server.server_port)
        if on_ready is None:
            print(f"Workspace UI: {address}", flush=True)
        else:
            on_ready(address, graph)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
