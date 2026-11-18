"""Authenticate and scope the installed application's Streamable HTTP MCP endpoint."""

from collections.abc import Callable
from concurrent.futures import TimeoutError as FuturesTimeoutError
from contextlib import asynccontextmanager, closing, contextmanager, suppress
from contextvars import ContextVar
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from secrets import token_urlsafe
import sqlite3
import stat
from subprocess import DEVNULL, PIPE, Popen
import sys
from tempfile import TemporaryDirectory
from threading import Event
from typing import Literal
from uuid import uuid4

from .execution import FlowBusy
from .node_sources import MAX_SOURCE_BYTES, SourceChanged, edit_source_file
from .sheet_locks import SheetLockedError
from .sheets import discover_flows
from .workspace_environment import workspace_interpreter


BEARER = re.compile(r"(?i:Bearer) +([a-zA-Z0-9_-]{43})")
TOKEN_PATH = re.compile(r"/mcp-tokens(?:/([0-9a-f]{32}))?")
MCP_BODY_LIMIT = 1024 * 1024
MCP_REQUEST_HEADERS = {"accept", "content-type", "mcp-protocol-version", "mcp-session-id"}
MCP_RESPONSE_HEADERS = {"allow", "content-type", "mcp-protocol-version", "mcp-session-id"}
EDITABLE_EXTENSIONS = {".py", ".json", ".toml", ".html", ".css", ".js", ".md", ".txt"}
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


def resolve_workspace_sheet(access: WorkspaceAccess, workspace_id: str, sheet: str):
    from mcp.server.mcpserver.exceptions import ToolError

    root = access.workspace(workspace_id)
    match = next((path for name, path in discover_flows(root) if name == sheet), None)
    if match is None or not available_root(match) or (match / "flow.py").is_symlink():
        raise ToolError(f"Unknown sheet {sheet!r}.")
    if match != root and not match.resolve().is_relative_to(root):
        raise ToolError("Sheet path is outside the Workspace.")
    try:
        resolver = access.sheet_resolver(workspace_id)
        return root, resolver(match)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        raise ToolError(str(error) or f"Could not open sheet {sheet!r}.") from None


def resolve_sheet_file(workspace_root: Path, flow_sheet, value: object, *, for_write: bool) -> tuple[Path, Path]:
    from mcp.server.mcpserver.exceptions import ToolError

    if not isinstance(value, str) or not value or "\x00" in value:
        raise ToolError("Expected a relative project path.")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ToolError("Path must stay inside the sheet directory.")
    if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
        raise ToolError("Hidden and environment files are not accessible.")
    sheet_root = flow_sheet.root.resolve()
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
    if for_write and current.suffix not in EDITABLE_EXTENSIONS:
        raise ToolError("Only project source and dashboard files can be edited.")
    return current, relative


def register_workspace_tools(server, access: WorkspaceAccess) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    @server.tool(description="List the Workspaces granted to this MCP identity.")
    def list_workspaces() -> dict:
        return {"workspaces": access.list()}

    @server.tool(description="Inspect the sheets in a granted Workspace without loading its flows.")
    def inspect_workspace(workspace_id: str) -> dict:
        root = access.workspace(workspace_id)
        sheets = [
            {"name": name, "entrypoint": (sheet / "flow.py").relative_to(root).as_posix()}
            for name, sheet in discover_flows(root)
            if available_root(sheet) and not (sheet / "flow.py").is_symlink()
        ]
        return {"id": workspace_id, "name": root.name, "sheets": sheets}

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

    @server.tool(description="Read a regular file inside a sheet directory, even when another editor holds the lock.")
    def read_file(workspace_id: str, path: str, sheet: str = "Main") -> dict:
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        target, relative = resolve_sheet_file(root, flow_sheet, path, for_write=False)
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

    def perform_sheet_edit(
        workspace_id: str,
        sheet: str,
        path: str,
        revision: str,
        session_id: str | None,
        build_source,
    ) -> dict:
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        target, relative = resolve_sheet_file(root, flow_sheet, path, for_write=True)
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
        _, relative = resolve_sheet_file(root, flow_sheet, path, for_write=True)
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
                    reader, lifeline = os.pipe()
                    supervisor = Path(__file__).with_name("agent_supervisor.py")
                    try:
                        process = Popen(
                            [sys.executable, "-I", str(supervisor), str(reader), "-1", "-1", *argv],
                            cwd=flow_sheet.root,
                            env=env,
                            stdin=DEVNULL,
                            stdout=DEVNULL,
                            stderr=PIPE,
                            start_new_session=True,
                            pass_fds=(reader,),
                        )
                    except BaseException:
                        os.close(lifeline)
                        lifeline = None
                        raise
                    finally:
                        os.close(reader)
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

    @server.tool(description="Stop an active flow run managed by the Workspace.")
    def stop_flow(workspace_id: str, sheet: str = "Main") -> dict:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            flow_sheet.stop()
        except (OSError, RuntimeError) as error:
            raise ToolError(str(error)) from None
        return {"sheet": sheet, "status": flow_sheet.status()}


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

    server = MCPServer("Phyzflow", log_level="WARNING")
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
