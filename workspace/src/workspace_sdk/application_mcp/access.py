from collections.abc import Callable
from contextlib import closing
from contextvars import ContextVar
from hashlib import sha256
import json
from pathlib import Path
import re
from secrets import token_urlsafe
import sqlite3
from threading import RLock
from uuid import uuid4

from ..sheets import find_sheet
from ..workspace_environment import is_protected_path

BEARER = re.compile(r"(?i:Bearer) +([a-zA-Z0-9_-]{43})")
TOKEN_PATH = re.compile(r"/mcp-tokens(?:/([0-9a-f]{32}))?")
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
        mounted_sheet_resolver: Callable[[str], Callable | None] = lambda _: None,
    ) -> None:
        self.tokens = tokens
        self.file_gate = RLock()
        self.registry = registry
        self.mount_sheet_resolver = mount_sheet_resolver
        self.mounted_sheet_resolver = mounted_sheet_resolver
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
    match = find_sheet(root, sheet)
    if match is None:
        raise ToolError(f"Unknown sheet {sheet!r}.")
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
