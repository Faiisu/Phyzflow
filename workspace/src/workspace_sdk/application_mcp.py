"""Authenticate and scope the installed application's Streamable HTTP MCP endpoint."""

from collections.abc import Callable
from contextlib import asynccontextmanager, closing, contextmanager
from contextvars import ContextVar
from functools import partial
from hashlib import sha256
import json
from pathlib import Path
import re
from secrets import token_urlsafe
import sqlite3
from uuid import uuid4

from .sheets import discover_flows


BEARER = re.compile(r"(?i:Bearer) +([a-zA-Z0-9_-]{43})")
TOKEN_PATH = re.compile(r"/mcp-tokens(?:/([0-9a-f]{32}))?")
MCP_BODY_LIMIT = 1024 * 1024
MCP_REQUEST_HEADERS = {"accept", "content-type", "mcp-protocol-version", "mcp-session-id"}
MCP_RESPONSE_HEADERS = {"allow", "content-type", "mcp-protocol-version", "mcp-session-id"}
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


def available_root(path: Path) -> bool:
    return path.is_dir() and path.resolve() == path


def register_workspace_tools(server, access: WorkspaceAccess) -> None:
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
        yield partial(portal.call, partial(dispatch, client))
