"""Authenticate and scope the installed application's Streamable HTTP MCP endpoint."""

from collections.abc import Callable
from concurrent.futures import TimeoutError as FuturesTimeoutError
from contextlib import asynccontextmanager, closing, contextmanager, suppress
from contextvars import ContextVar
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import re
from secrets import token_urlsafe
import sqlite3
import stat
from subprocess import DEVNULL, PIPE, Popen
from tempfile import TemporaryDirectory
from threading import Event
from typing import Literal
from uuid import uuid4

from .agent_supervisor import spawn_supervised
from .console import ConsoleLogStore
from .dashboard import DASHBOARD_RESIZE_GUIDANCE, inspect_sheet_dashboards
from .execution import FlowBusy
from .history import RunHistory
from .node_sources import MAX_SOURCE_BYTES, SourceChanged, edit_source_file
from .sheet_locks import SheetLockedError
from .sheets import discover_flows
from .workspace_environment import (
    install_workspace_packages, is_protected_path, workspace_interpreter,
)


BEARER = re.compile(r"(?i:Bearer) +([a-zA-Z0-9_-]{43})")
TOKEN_PATH = re.compile(r"/mcp-tokens(?:/([0-9a-f]{32}))?")
RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
MCP_BODY_LIMIT = 1024 * 1024
MCP_REQUEST_HEADERS = {"accept", "content-type", "mcp-protocol-version", "mcp-session-id"}
MCP_RESPONSE_HEADERS = {"allow", "content-type", "mcp-protocol-version", "mcp-session-id"}
EDITABLE_EXTENSIONS = {".py", ".json", ".toml", ".html", ".css", ".js", ".md", ".txt"}
MAX_ENTRIES = 500
MAX_SCAN_FILES = 2000
MAX_SEARCH_MATCHES = 100
request_identity: ContextVar[str] = ContextVar("mcp_identity")


class McpTokens:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "mcp.sqlite3"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS tokens ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, verifier BLOB NOT NULL UNIQUE, workspace_ids TEXT NOT NULL)"
            )

    def entries(self) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as db:
            return [
                {"id": identity, "name": name, "workspace_ids": json.loads(workspaces)}
                for identity, name, workspaces in db.execute("SELECT id, name, workspace_ids FROM tokens ORDER BY rowid")
            ]

    def issue(self, name: object, workspace_ids: list[str]) -> dict[str, str]:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or not name.strip().isprintable():
            raise ValueError("Enter a client name up to 80 characters.")
        if not workspace_ids:
            raise ValueError("Select at least one Workspace.")
        identity, token = uuid4().hex, token_urlsafe(32)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO tokens VALUES (?, ?, ?, ?)", (
                identity, name.strip(), sha256(token.encode()).digest(), json.dumps(workspace_ids),
            ))
        return {"id": identity, "token": token}

    def reassign(self, identity: str, workspace_ids: list[str]) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            cursor = db.execute("UPDATE tokens SET workspace_ids = ? WHERE id = ?", (json.dumps(workspace_ids), identity))
            if not cursor.rowcount:
                raise KeyError(identity)

    def revoke(self, identity: str) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            cursor = db.execute("DELETE FROM tokens WHERE id = ?", (identity,))
            if not cursor.rowcount:
                raise KeyError(identity)

    def authenticate(self, headers: list[str]) -> str | None:
        if len(headers) != 1 or not (match := BEARER.fullmatch(headers[0])):
            return None
        verifier = sha256(match[1].encode()).digest()
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT id FROM tokens WHERE verifier = ?", (verifier,)).fetchone()
        return row[0] if row else None

    def workspace_ids(self, identity: str) -> set[str] | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT workspace_ids FROM tokens WHERE id = ?", (identity,)).fetchone()
        return set(json.loads(row[0])) if row else None

    def client_name(self, identity: str) -> str | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT name FROM tokens WHERE id = ?", (identity,)).fetchone()
        return row[0] if row else None


class WorkspaceAccess:
    def __init__(
        self, tokens: McpTokens, registry, mount_sheet_resolver,
        is_deleting: Callable[[str], bool] = lambda _: False,
    ) -> None:
        self.tokens = tokens
        self.registry = registry
        self.mount_sheet_resolver = mount_sheet_resolver
        self.is_deleting = is_deleting
        self.protected_folders: tuple[Path, ...] = tuple(
            folder.resolve() for folder, _ in getattr(registry, "protected_folders", ())
        )

    def principal(self) -> str:
        return request_identity.get()

    def allowed_ids(self) -> set[str]:
        from mcp.server.mcpserver.exceptions import ToolError

        allowed = self.tokens.workspace_ids(self.principal())
        if allowed is None:
            raise ToolError("MCP token was revoked.")
        return allowed

    def list(self) -> list[dict[str, str]]:
        allowed = self.allowed_ids()
        return [
            {"id": item["id"], "name": item["name"]} for item in self.registry.entries()
            if item["id"] in allowed and not self.is_deleting(item["id"]) and available_root(Path(item["path"]))
        ]

    def workspace(self, workspace_id: str) -> Path:
        from mcp.server.mcpserver.exceptions import ToolError

        if self.is_deleting(workspace_id):
            raise ToolError("This Workspace is being deleted.")
        if workspace_id in self.allowed_ids():
            root = self.registry.get(workspace_id)
            if root is not None and available_root(root):
                return root
        raise ToolError("Workspace access denied.")

    def sheet_resolver(self, workspace_id: str):
        from mcp.server.mcpserver.exceptions import ToolError

        try:
            return self.mount_sheet_resolver(workspace_id, self.workspace(workspace_id))
        except RuntimeError as error:
            raise ToolError(str(error)) from None

    def owner_label(self) -> str:
        from mcp.server.mcpserver.exceptions import ToolError

        name = self.tokens.client_name(self.principal())
        if name is None:
            raise ToolError("MCP token was revoked.")
        return f"MCP client ({name})"


def available_root(path: Path) -> bool:
    return path.is_dir() and path.resolve() == path


def ensure_not_protected(path: Path, protected_folders: tuple[Path, ...]) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    if is_protected_path(path, protected_folders):
        raise ToolError("Protected application paths are not accessible.")


def resolve_workspace_sheet(access: WorkspaceAccess, workspace_id: str, sheet: str):
    from mcp.server.mcpserver.exceptions import ToolError

    root = access.workspace(workspace_id)
    ensure_not_protected(root, access.protected_folders)
    match = next((path for name, path in discover_flows(root) if name == sheet), None)
    if match is None or not available_root(match) or (match / "flow.py").is_symlink():
        raise ToolError(f"Unknown sheet {sheet!r}.")
    if match != root:
        if (root / "flows").is_symlink() or not match.resolve().is_relative_to(root):
            raise ToolError("Sheet path is outside the Workspace.")
    ensure_not_protected(match, access.protected_folders)
    try:
        resolver = access.sheet_resolver(workspace_id)
        return root, resolver(match)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        raise ToolError(str(error) or f"Could not open sheet {sheet!r}.") from None


def resolve_sheet_file(
    workspace_root: Path,
    flow_sheet,
    value: object,
    *,
    for_write: bool,
    allow_root: bool = False,
    protected_folders: tuple[Path, ...] = (),
) -> tuple[Path, Path]:
    from mcp.server.mcpserver.exceptions import ToolError

    if not isinstance(value, str) or not value or "\x00" in value:
        raise ToolError("Expected a relative project path.")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ToolError("Path must stay inside the sheet directory.")
    sheet_root = flow_sheet.root.resolve()
    if not relative.parts:
        if not allow_root or value not in (".", "./"):
            raise ToolError("Path must stay inside the sheet directory.")
        if flow_sheet.root.is_symlink():
            raise ToolError("Symbolic links are not accessible.")
        ensure_not_protected(sheet_root, protected_folders)
        return sheet_root, Path(".")
    if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
        raise ToolError("Hidden and environment files are not accessible.")
    if flow_sheet.main and relative.parts[0] == "flows" and for_write:
        raise ToolError("Specify the target sheet instead of writing under flows/ from Main.")
    base = workspace_root if (flow_sheet.main and relative.parts[0] == "flows") else sheet_root
    current = base
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ToolError("Symbolic links are not accessible.")
    resolved = current.resolve(strict=False)
    if not resolved.is_relative_to(base):
        raise ToolError("Path must stay inside the sheet directory.")
    ensure_not_protected(current, protected_folders)
    if for_write and current.suffix not in EDITABLE_EXTENSIONS:
        raise ToolError("Only project source and dashboard files can be edited.")
    return current, relative


def _ensure_worker_url(flow_sheet) -> str:
    from mcp.server.mcpserver.exceptions import ToolError
    from .workspace_ui import ReloadConflict

    try:
        return flow_sheet.ensure_worker_url()
    except (ReloadConflict, OSError, RuntimeError, ValueError, EOFError) as error:
        raise ToolError(str(error) or "Could not open the flow worker.") from None


def _worker_json(flow_sheet, method: str, path: str, payload: dict | None = None) -> dict:
    from mcp.server.mcpserver.exceptions import ToolError

    url = _ensure_worker_url(flow_sheet)
    body = None
    headers = {}
    if payload is not None:
        try:
            body = json.dumps(payload, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ToolError(str(error) or "Invalid JSON request payload.") from None
        headers["Content-Type"] = "application/json"

    connection = HTTPConnection(url.removeprefix("http://"), timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers)
        with connection.getresponse() as response:
            raw = response.read()
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
            if response.status >= 400:
                message = data.get("error") if isinstance(data, dict) else None
                raise ToolError(message or f"Flow worker returned {response.status}.")
            if not isinstance(data, dict):
                raise ToolError("Flow worker returned an unexpected response.")
            return data
    except (OSError, HTTPException) as error:
        raise ToolError(f"Could not reach the flow worker: {error}") from None
    finally:
        connection.close()


def _live_run_events(flow_sheet, run_id: str) -> list[dict]:
    worker = flow_sheet.worker
    if worker is None or not worker.url or worker.process.poll() is not None:
        return []
    connection = HTTPConnection(worker.url.removeprefix("http://"), timeout=2)
    try:
        connection.request("GET", "/live-run")
        with connection.getresponse() as response:
            if response.status != 200:
                return []
            payload = json.loads(response.read())
    except (OSError, HTTPException, ValueError):
        return []
    finally:
        connection.close()
    items = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    events = []
    for entry in items:
        if (
            isinstance(entry, (list, tuple))
            and len(entry) == 2
            and isinstance(entry[1], dict)
            and entry[1].get("run_id") == run_id
        ):
            events.append(entry[1])
    return events


def _summarize_attempt(attempt, flow_sheet) -> dict:
    events = attempt.snapshot_events()
    if attempt.status == "running":
        live_events = _live_run_events(flow_sheet, attempt.run_id)
        if live_events:
            events = live_events + [
                event for event in events if event.get("kind") != "console"
            ]
    steps: dict[str, dict] = {}
    result = None
    error = attempt.start_error
    started_at = None
    finished_at = None
    for event in events:
        if event.get("kind") == "console":
            continue
        if "result" in event:
            result = event["result"]
        if "error" in event and isinstance(event["error"], str):
            error = event["error"]
        step_id = event.get("step_id")
        if not isinstance(step_id, str):
            status = event.get("status")
            if status == "running" and started_at is None:
                started_at = event.get("time")
            elif status in ("succeeded", "failed", "stopped"):
                finished_at = event.get("time")
            continue
        step = steps.setdefault(step_id, {"id": step_id})
        if event.get("status") == "running" and "started_at" not in step:
            step["started_at"] = event.get("time")
        if event.get("status") in ("succeeded", "failed", "stopped", "skipped"):
            step["finished_at"] = event.get("time")
        for key in (
            "status", "duration_ms", "file", "line", "source_target",
            "error_type", "diagnostic_error_type", "traceback",
            "blocked_by", "metrics", "reasons", "output_preview",
        ):
            if key in event:
                step[key] = event[key]
        if "live_metrics" in event and isinstance(event["live_metrics"], dict):
            live = step.setdefault("live_metrics", {})
            live.update(event["live_metrics"])
        if "setting_change" in event:
            changes = step.setdefault("setting_changes", [])
            changes.append(event["setting_change"])
    return {
        "run_id": attempt.run_id,
        "status": attempt.status,
        "retry_count": attempt.retry_count,
        "crash_exhausted": attempt.crash_exhausted,
        "start_error": attempt.start_error,
        "started_at": started_at,
        "finished_at": finished_at,
        "steps": list(steps.values()),
        "result": result,
        "error": error,
    }


def register_workspace_tools(server, access: WorkspaceAccess) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    @server.tool(description="List the Workspaces granted to this MCP identity.")
    def list_workspaces() -> dict:
        return {"workspaces": access.list()}

    @server.tool(description="Inspect the sheets in a granted Workspace without loading its flows.")
    def inspect_workspace(workspace_id: str) -> dict:
        root = access.workspace(workspace_id)
        ensure_not_protected(root, access.protected_folders)
        sheets = [
            {"name": name, "entrypoint": (sheet / "flow.py").relative_to(root).as_posix()}
            for name, sheet in discover_flows(root)
            if available_root(sheet) and not (sheet / "flow.py").is_symlink()
        ]
        return {"id": workspace_id, "name": root.name, "sheets": sheets}

    @server.tool(description="Create a new flow sheet in a granted Workspace.")
    def create_sheet(workspace_id: str, name: str) -> dict:
        root = access.workspace(workspace_id)
        ensure_not_protected(root, access.protected_folders)
        try:
            resolver = access.sheet_resolver(workspace_id)
            created = resolver.create_workspace_sheet(name)
        except (FileExistsError, OSError, RuntimeError, ValueError) as error:
            raise ToolError(str(error) or "Could not create sheet.") from None
        return {"workspace_id": workspace_id, **created}

    @server.tool(description="Delete a secondary flow sheet in a granted Workspace, moving it to .workspace/deleted-flows.")
    def delete_sheet(workspace_id: str, sheet: str, session_id: str | None = None) -> dict:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            resolver = access.sheet_resolver(workspace_id)
            result = resolver.delete_workspace_sheet(
                flow_sheet.root,
                principal=access.principal(),
                session_id=session_id,
            )
        except (SheetLockedError, FileNotFoundError, OSError, RuntimeError, ValueError) as error:
            raise ToolError(str(error) or f"Could not delete sheet {sheet!r}.") from None
        return {"sheet": sheet, **result}

    @server.tool(description="Inspect a sheet's dashboard panels, asset paths, theme contract, and resize guidance.")
    def inspect_dashboards(workspace_id: str, sheet: str = "Main") -> dict:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        return {"sheet": sheet, **inspect_sheet_dashboards(flow_sheet.root)}

    @server.tool(description="Open a sheet for exclusive editing or read-only inspection.")
    def open_sheet(workspace_id: str, sheet: str = "Main", mode: Literal["edit", "read"] = "edit") -> dict:
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        entrypoint = (flow_sheet.root / "flow.py").relative_to(root).as_posix()
        if mode == "read":
            lock_status = flow_sheet.edit_lease.status()
            attempt = flow_sheet.attempt
            return {
                "sheet": sheet,
                "mode": "read",
                "entrypoint": entrypoint,
                "status": flow_sheet.status(),
                "run_id": attempt.run_id if attempt is not None else None,
                "read_only": lock_status["locked"],
                "edit_lock": lock_status,
            }
        try:
            lock_status = flow_sheet.edit_lease.acquire(
                principal=access.principal(),
                principal_type="mcp",
                owner=access.owner_label(),
            )
        except (SheetLockedError, ValueError, OSError) as error:
            raise ToolError(str(error)) from None
        return {
            "session_id": lock_status["session_id"],
            "sheet": sheet,
            "mode": "edit",
            "entrypoint": entrypoint,
            "edit_lock": lock_status,
        }

    @server.tool(description="Refresh the heartbeat for an active sheet editing session.")
    def heartbeat_sheet(workspace_id: str, sheet: str = "Main", session_id: str = "") -> dict:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            return flow_sheet.edit_lease.heartbeat(access.principal(), session_id)
        except (SheetLockedError, ValueError, OSError) as error:
            raise ToolError(str(error)) from None

    @server.tool(description="Close an active sheet editing session, clean up its processes, and release the lock.")
    def close_sheet(
        workspace_id: str,
        sheet: str = "Main",
        session_id: str = "",
        outcome: Literal["succeeded", "failed", "cancelled"] = "succeeded",
    ) -> dict:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            return flow_sheet.edit_lease.release(access.principal(), session_id, outcome=outcome)
        except (SheetLockedError, ValueError, OSError) as error:
            raise ToolError(str(error)) from None

    @server.tool(description="List immediate regular files and subdirectories inside a sheet directory.")
    def list_files(workspace_id: str, path: str = ".", sheet: str = "Main") -> dict:
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        target, relative = resolve_sheet_file(
            root,
            flow_sheet,
            path,
            for_write=False,
            allow_root=True,
            protected_folders=access.protected_folders,
        )
        if not target.is_dir() or target.is_symlink():
            raise ToolError("Expected an accessible directory inside the sheet.")
        base = root if (flow_sheet.main and relative.parts and relative.parts[0] == "flows") else flow_sheet.root.resolve()
        entries = []
        truncated = False
        try:
            with os.scandir(target) as children:
                items = sorted(children, key=lambda child: child.name)
        except OSError as error:
            raise ToolError(f"Could not list {relative.as_posix()}: {error.strerror or error}.") from None
        for child in items:
            if child.name.startswith(".") or child.name == "__pycache__" or child.is_symlink():
                continue
            is_dir = child.is_dir(follow_symlinks=False)
            is_file = child.is_file(follow_symlinks=False)
            if not is_dir and not is_file:
                continue
            child_path = Path(child.path)
            if is_dir and (child_path / "pyvenv.cfg").is_file():
                continue
            if len(entries) == MAX_ENTRIES:
                truncated = True
                break
            entries.append({
                "path": child_path.relative_to(base).as_posix(),
                "kind": "directory" if is_dir else "file",
            })
        return {
            "sheet": sheet,
            "path": relative.as_posix(),
            "entries": entries,
            "truncated": truncated,
        }

    @server.tool(description="Read a regular file inside a sheet directory, even when another editor holds the lock.")
    def read_file(workspace_id: str, path: str, sheet: str = "Main") -> dict:
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        target, relative = resolve_sheet_file(
            root,
            flow_sheet,
            path,
            for_write=False,
            protected_folders=access.protected_folders,
        )
        if not target.is_file():
            raise ToolError("Only regular files can be read.")
        try:
            with target.open("rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ToolError("Only regular files can be read.")
                data = stream.read(MAX_SOURCE_BYTES + 1)
        except OSError as error:
            raise ToolError(f"Could not read {relative.as_posix()}: {error.strerror or error}.") from None
        truncated = len(data) > MAX_SOURCE_BYTES
        content_bytes = data[:MAX_SOURCE_BYTES]

        return {
            "path": relative.as_posix(),
            "sheet": sheet,
            "content": content_bytes.decode("utf-8", errors="replace"),
            "revision": None if truncated else sha256(content_bytes).hexdigest(),
            "truncated": truncated,
        }

    @server.tool(description="Search editable source and dashboard files in a sheet for a literal text query.")
    def search_code(workspace_id: str, query: str, sheet: str = "Main") -> dict:
        if not isinstance(query, str) or not 1 <= len(query) <= 512:
            raise ToolError("query must contain 1 to 512 characters.")
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        sheet_root = flow_sheet.root.resolve()
        matches: list[dict] = []
        scanned = 0
        truncated = False
        pending = [sheet_root]
        while pending:
            directory = pending.pop()
            try:
                with os.scandir(directory) as children:
                    items = sorted(children, key=lambda child: child.name, reverse=True)
            except OSError:
                continue
            for child in items:
                if scanned >= MAX_SCAN_FILES:
                    return {"sheet": sheet, "matches": matches, "truncated": True}
                scanned += 1
                if child.is_symlink() or child.name.startswith(".") or child.name == "__pycache__":
                    continue
                child_path = Path(child.path)
                if flow_sheet.main and directory == sheet_root and child.name == "flows":
                    continue
                if is_protected_path(child_path, access.protected_folders):
                    continue
                if child.is_dir(follow_symlinks=False):
                    if not (child_path / "pyvenv.cfg").is_file():
                        pending.append(child_path)
                    continue
                if not child.is_file(follow_symlinks=False) or child_path.suffix not in EDITABLE_EXTENSIONS:
                    continue
                try:
                    with child_path.open("rb") as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            continue
                        data = stream.read(MAX_SOURCE_BYTES + 1)
                except OSError:
                    continue
                if len(data) > MAX_SOURCE_BYTES:
                    truncated = True
                    data = data[:MAX_SOURCE_BYTES]
                relative = child_path.relative_to(sheet_root).as_posix()
                text_content = data.decode("utf-8", errors="replace")
                for line_no, text in enumerate(text_content.splitlines(), 1):
                    if query in text:
                        if len(text) > 1024:
                            truncated = True
                        matches.append({"path": relative, "line": line_no, "text": text[:1024]})
                        if len(matches) >= MAX_SEARCH_MATCHES:
                            return {"sheet": sheet, "matches": matches, "truncated": True}
        return {"sheet": sheet, "matches": matches, "truncated": truncated}

    def perform_sheet_edit(
        workspace_id: str,
        sheet: str,
        path: str,
        revision: str,
        session_id: str | None,
        build_source,
    ) -> dict:
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        target, relative = resolve_sheet_file(
            root,
            flow_sheet,
            path,
            for_write=True,
            protected_folders=access.protected_folders,
        )
        if not isinstance(revision, str) or not revision:
            raise ToolError("An expected revision is required.")
        principal = access.principal()

        def apply_edit() -> dict:
            exists = target.exists()
            if not exists and revision != "missing":
                raise SourceChanged
            before = edit_source_file(target, None, None, initial="" if not exists else None)
            if before["revision"] != revision:
                raise SourceChanged
            source = build_source(before["source"], exists)
            if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
                raise ValueError("Source must fit in 128 KiB.")
            target.parent.mkdir(parents=True, exist_ok=True)
            updated = edit_source_file(target, source, revision, initial="" if not exists else None)
            return {
                "path": relative.as_posix(),
                "sheet": sheet,
                "revision": updated["revision"],
                "created": not exists,
            }

        try:
            return flow_sheet.edit_lease.write_locked(
                principal, session_id, apply_edit, path=relative.as_posix(),
            )
        except SourceChanged:
            raise ToolError("Source changed. Read the latest revision before editing.") from None
        except (SheetLockedError, ValueError, UnicodeError, SyntaxError, OSError) as error:
            raise ToolError(str(error)) from None

    @server.tool(description="Stage an unsaved draft in an active sheet editing session without modifying disk.")
    def stage_draft(
        workspace_id: str,
        path: str,
        source: str,
        sheet: str = "Main",
        session_id: str = "",
    ) -> dict:
        if not isinstance(source, str):
            raise ToolError("source must be text.")
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        _, relative = resolve_sheet_file(
            root,
            flow_sheet,
            path,
            for_write=True,
            protected_folders=access.protected_folders,
        )
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ToolError("Source must fit in 128 KiB.")
        try:
            lock_status = flow_sheet.edit_lease.stage_draft(
                access.principal(), session_id, relative.as_posix(), source,
            )
        except (SheetLockedError, ValueError, OSError) as error:
            raise ToolError(str(error)) from None
        return {
            "path": relative.as_posix(),
            "sheet": sheet,
            "staged": True,
            "edit_lock": lock_status,
        }

    @server.tool(description="Create (revision='missing') or replace a file in a sheet while holding its edit lock.")
    def write_file(
        workspace_id: str,
        path: str,
        revision: str,
        source: str,
        sheet: str = "Main",
        session_id: str | None = None,
    ) -> dict:
        if not isinstance(source, str):
            raise ToolError("source must be text.")
        return perform_sheet_edit(workspace_id, sheet, path, revision, session_id, lambda _before, _exists: source)

    @server.tool(description="Replace one exact occurrence in an existing sheet file while holding its edit lock.")
    def apply_patch(
        workspace_id: str,
        path: str,
        revision: str,
        old_text: str,
        new_text: str,
        sheet: str = "Main",
        session_id: str | None = None,
    ) -> dict:
        if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
            raise ToolError("A patch requires nonempty old_text and new_text.")

        def replace_once(before_source: str, exists: bool) -> str:
            if not exists:
                raise SourceChanged
            if before_source.count(old_text) != 1:
                raise ValueError("old_text must match exactly one location.")
            return before_source.replace(old_text, new_text, 1)

        return perform_sheet_edit(workspace_id, sheet, path, revision, session_id, replace_once)

    @server.tool(description="Validate a sheet flow inside the Workspace Python environment.")
    async def validate_flow(workspace_id: str, sheet: str = "Main", session_id: str | None = None) -> dict:
        from anyio import CancelScope, get_cancelled_exc_class, to_thread

        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        interpreter = workspace_interpreter(root)
        if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
            raise ToolError("Workspace Python environment is unavailable.")
        principal = access.principal()
        if session_id is not None:
            try:
                flow_sheet.edit_lease.verify_write(principal, session_id)
            except (SheetLockedError, ValueError, OSError) as error:
                raise ToolError(str(error)) from None

        with TemporaryDirectory(prefix="phyzflow-check-") as temp_dir:
            result_path = Path(temp_dir) / "result.json"
            worker = Path(__file__).with_name("sheet_worker.py")
            argv = [str(interpreter), "-I", str(worker), "--check", str(result_path)]
            env = {
                **{key: value for key, value in os.environ.items() if not key.startswith("PHYZFLOW_")},
                "PATH": f"{interpreter.parent}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
                "VIRTUAL_ENV": str(interpreter.parent.parent),
                "PYTHONNOUSERSITE": "1",
                "PYTHONUNBUFFERED": "1",
            }
            lifeline = None
            process = None
            registered = False
            disconnected = False
            try:
                if os.name == "posix":
                    process, lifeline = spawn_supervised(
                        argv,
                        cwd=flow_sheet.root,
                        env=env,
                        stdin=DEVNULL,
                        stdout=DEVNULL,
                        stderr=PIPE,
                    )
                else:
                    process = Popen(
                        argv,
                        cwd=flow_sheet.root,
                        env=env,
                        stdin=DEVNULL,
                        stdout=DEVNULL,
                        stderr=PIPE,
                    )
                if session_id is not None:
                    try:
                        flow_sheet.edit_lease.register_process(session_id, process, lifeline)
                        registered = True
                    except (SheetLockedError, ValueError, OSError) as error:
                        raise ToolError(str(error)) from None
                _, stderr = await to_thread.run_sync(process.communicate, abandon_on_cancel=True)
            except BaseException as error:
                if isinstance(error, get_cancelled_exc_class()):
                    disconnected = True
                raise
            finally:
                with CancelScope(shield=True):
                    if process is not None:
                        flow_sheet.edit_lease.cleanup_process(
                            session_id if registered else None,
                            process,
                            lifeline,
                        )
                    elif lifeline is not None:
                        with suppress(OSError):
                            os.close(lifeline)
                    if disconnected and session_id is not None:
                        flow_sheet.edit_lease.mark_disconnected(session_id)
            if session_id is not None and flow_sheet.edit_lease.is_invalidated(session_id):
                raise ToolError("This editing session is no longer active.")
            if result_path.is_file():
                data = json.loads(result_path.read_text(encoding="utf-8"))
                return {"sheet": sheet, **data}
            detail = (stderr or b"").decode("utf-8", errors="replace").strip()
            return {
                "sheet": sheet,
                "valid": False,
                "stage": "import",
                "error": detail[:4096] or "Flow validation did not complete.",
            }

    @server.tool(description="Read declared node setting definitions and current saved values for a sheet.")
    async def read_node_settings(workspace_id: str, sheet: str = "Main") -> dict:
        from anyio import to_thread

        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        settings = await to_thread.run_sync(
            _worker_json, flow_sheet, "GET", "/dashboard-settings/workflow", None,
        )
        return {"sheet": sheet, "settings": settings}

    @server.tool(description="Save a node setting for the next run or queue a live setting update on an active run.")
    async def update_node_setting(
        workspace_id: str,
        node_id: str,
        name: str,
        value: bool | int | float | str,
        sheet: str = "Main",
        mode: Literal["next", "live"] = "next",
        run_id: str | None = None,
    ) -> dict:
        from anyio import to_thread

        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        target_run_id = run_id
        if mode == "live" and target_run_id is None:
            attempt = flow_sheet.attempt
            target_run_id = attempt.run_id if attempt is not None and attempt.status == "running" else None
        payload = {
            "nodeId": node_id,
            "name": name,
            "mode": mode,
            "value": value,
            "runId": target_run_id,
        }
        result = await to_thread.run_sync(
            _worker_json, flow_sheet, "POST", "/dashboard-settings/workflow", payload,
        )
        return {"sheet": sheet, "node_id": node_id, **result}

    @server.tool(description="Start a flow run managed by the Workspace lifecycle.")
    def run_flow(workspace_id: str, sheet: str = "Main", input: dict | None = None) -> dict:
        from .workspace_ui import ReloadConflict

        if input is not None and not isinstance(input, dict):
            raise ToolError("Flow input must be a JSON object.")
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            attempt = flow_sheet.start_run(input)
        except (FlowBusy, ReloadConflict, OSError, RuntimeError, ValueError) as error:
            raise ToolError(str(error)) from None
        return {"run_id": attempt.run_id, "sheet": sheet, "status": attempt.status}

    @server.tool(description="Start every idle sheet in a granted Workspace concurrently without disturbing running sheets.")
    async def run_all_flows(workspace_id: str) -> dict:
        from anyio import to_thread

        root = access.workspace(workspace_id)
        ensure_not_protected(root, access.protected_folders)
        try:
            resolver = access.sheet_resolver(workspace_id)
            result = await to_thread.run_sync(resolver.run_all_sheets)
        except (OSError, RuntimeError, ValueError) as error:
            raise ToolError(str(error) or "Could not run Workspace sheets.") from None
        return {"workspace_id": workspace_id, **result}

    @server.tool(description="Stop an active flow run managed by the Workspace.")
    def stop_flow(workspace_id: str, sheet: str = "Main") -> dict:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            flow_sheet.stop()
        except (OSError, RuntimeError) as error:
            raise ToolError(str(error)) from None
        return {"sheet": sheet, "status": flow_sheet.status()}

    @server.tool(description="Inspect active run status, step timing and diagnostics, and run history for a sheet.")
    def inspect_execution(workspace_id: str, sheet: str = "Main", run_id: str | None = None) -> dict:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        history = RunHistory(flow_sheet.root)
        try:
            recent_runs = history.list_runs()[:20]
        except (OSError, ValueError) as error:
            raise ToolError(str(error) or "Could not read run history.") from None
        attempt = flow_sheet.attempt
        if run_id is not None:
            if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
                raise ToolError("Invalid run ID.")
            try:
                record = history.read(run_id)
            except (OSError, ValueError) as error:
                raise ToolError(str(error) or "Could not read run history.") from None
            active_run = _summarize_attempt(attempt, flow_sheet) if attempt is not None and attempt.run_id == run_id else None
            if record is None and active_run is None:
                raise ToolError(f"Unknown run {run_id!r}.")
            return {
                "sheet": sheet,
                "status": flow_sheet.status(),
                "run_id": run_id,
                "active_run": active_run,
                "history": record,
                "recent_runs": recent_runs,
            }
        target_run_id = attempt.run_id if attempt is not None else (recent_runs[0]["id"] if recent_runs else None)
        try:
            record = history.read(target_run_id) if target_run_id is not None else None
        except (OSError, ValueError) as error:
            raise ToolError(str(error) or "Could not read run history.") from None
        active_run = _summarize_attempt(attempt, flow_sheet) if attempt is not None else None
        return {
            "sheet": sheet,
            "status": flow_sheet.status(),
            "run_id": target_run_id,
            "active_run": active_run,
            "history": record,
            "recent_runs": recent_runs,
        }

    @server.tool(description="Read saved or live console logs and current log settings for a sheet.")
    def read_console_logs(workspace_id: str, sheet: str = "Main", run_id: str | None = None) -> dict:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        store = ConsoleLogStore(flow_sheet.root)
        try:
            current_settings = store.settings()
            attempt = flow_sheet.attempt
            live_events = []
            if attempt is not None and (run_id is None or attempt.run_id == run_id):
                live_events = [
                    event for event in attempt.snapshot_events()
                    if event.get("kind") == "console"
                ]
            target_run_id = run_id
            if target_run_id is None and attempt is not None:
                saved_events = store.read_run(attempt.run_id)
                if saved_events or live_events or attempt.status == "running" or not current_settings["save_logs"]:
                    target_run_id = attempt.run_id
            else:
                saved_events = store.read_run(target_run_id) if target_run_id is not None else []

            if target_run_id is not None:
                resolved = target_run_id
                if not saved_events or (not current_settings["save_logs"] and len(live_events) > len(saved_events)):
                    events = live_events
                else:
                    events = saved_events
            else:
                events = store.read_run(None)
                resolved = events[0]["run_id"] if events else None
        except (ValueError, OSError) as error:
            raise ToolError(str(error) or "Could not read console logs.") from None
        stdout = "".join(event["text"] for event in events if event.get("stream") == "stdout")
        stderr = "".join(event["text"] for event in events if event.get("stream") == "stderr")
        return {
            "sheet": sheet,
            "settings": current_settings,
            "run_id": resolved,
            "events": events,
            "stdout": stdout,
            "stderr": stderr,
        }

    @server.tool(description="Update per-sheet console log saving and retention settings.")
    def configure_console_logs(
        workspace_id: str,
        sheet: str = "Main",
        save_logs: bool | None = None,
        retention_days: int | None = None,
    ) -> dict:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        changes: dict[str, bool | int] = {}
        if save_logs is not None:
            changes["save_logs"] = save_logs
        if retention_days is not None:
            changes["retention_days"] = retention_days
        try:
            updated = ConsoleLogStore(flow_sheet.root).update_settings(changes)
        except (ValueError, OSError) as error:
            raise ToolError(str(error) or "Could not save console log settings.") from None
        return {"sheet": sheet, "settings": updated}

    @server.tool(description="Install Python package dependencies into a granted Workspace's shared .venv interpreter.")
    async def install_dependencies(workspace_id: str, packages: list[str]) -> dict:
        from anyio import to_thread

        root = access.workspace(workspace_id)
        ensure_not_protected(root, access.protected_folders)
        try:
            result = await to_thread.run_sync(
                lambda: install_workspace_packages(
                    root,
                    packages,
                    protected_folders=access.protected_folders,
                )
            )
        except (ValueError, RuntimeError, OSError) as error:
            raise ToolError(str(error) or "Could not install Workspace packages.") from None
        return {"workspace_id": workspace_id, **result}


@contextmanager
def mcp_transport(access: WorkspaceAccess):
    try:
        from mcp.server import MCPServer
    except ModuleNotFoundError as error:
        if error.name != "mcp":
            raise
        raise ValueError("Install workspace-sdk[mcp] to run the Phyzflow application.") from None

    from anyio.from_thread import start_blocking_portal
    from httpx2 import ASGITransport, AsyncClient

    server = MCPServer(
        "Phyzflow",
        instructions=(
            "Before creating or editing a dashboard panel, call inspect_dashboards for its layout and theme contract. "
            f"{DASHBOARD_RESIZE_GUIDANCE}"
        ),
        log_level="WARNING",
    )
    register_workspace_tools(server, access)
    app = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, max_request_body_size=MCP_BODY_LIMIT,
    )

    @asynccontextmanager
    async def open_client():
        async with server.session_manager.run(), AsyncClient(
            base_url="http://127.0.0.1:8080", transport=ASGITransport(app=app, raise_app_exceptions=False),
        ) as client:
            yield client

    async def dispatch(client, identity, method, headers, body):
        credential = request_identity.set(identity)
        try:
            return await client.request(method, "/mcp", headers=headers, content=body)
        finally:
            request_identity.reset(credential)

    with start_blocking_portal() as portal, portal.wrap_async_context_manager(open_client()) as client:
        def send_mcp(identity, method, headers, body, *, disconnected=None):
            import anyio

            scope_holder = []
            ready = Event()
            done = Event()

            async def run_dispatch():
                with anyio.CancelScope() as scope:
                    scope_holder.append(scope)
                    ready.set()
                    try:
                        return await dispatch(client, identity, method, headers, body)
                    finally:
                        done.set()

            future = portal.start_task_soon(run_dispatch)
            while True:
                try:
                    return future.result(timeout=0.05)
                except FuturesTimeoutError:
                    if disconnected is not None and disconnected():
                        ready.wait(timeout=1)
                        if scope_holder:
                            with suppress(RuntimeError):
                                portal.call(scope_holder[0].cancel)
                        done.wait(timeout=5)
                        return None

        yield send_mcp
