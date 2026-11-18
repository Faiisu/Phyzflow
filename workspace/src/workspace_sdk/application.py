"""Host the landing page and registered Workspace routes on one origin."""

from contextlib import closing, suppress
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
import re
from shutil import rmtree
import sqlite3
import subprocess
from threading import Lock
from urllib.parse import parse_qs
from uuid import uuid4

from .application_auth import Administrator
from .application_mcp import (
    MCP_BODY_LIMIT, MCP_REQUEST_HEADERS, MCP_RESPONSE_HEADERS, TOKEN_PATH,
    McpTokens, WorkspaceAccess, mcp_transport,
)
from .sheets import discover_flows, initialize_workspace
from .ui_settings import load_ui_settings
from .web import STATIC, UIRequestHandler, ui_address
from .workspace_environment import ensure_workspace_environment, workspace_interpreter
from .workspace_ui import workspace_routes


WORKSPACE_PATH = re.compile(r"/workspaces/([0-9a-f]{32})(/.*)?")
ASSETS = {
    "/": ("landing.html", "text/html"),
    "/landing.js": ("landing.js", "text/javascript"),
    "/mcp-access.js": ("mcp-access.js", "text/javascript"),
    "/workspace.css": ("workspace.css", "text/css"),
}


def same_folder(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return left.samefile(right)
    except (FileNotFoundError, NotADirectoryError):
        return False


def folders_overlap(left: Path, right: Path) -> bool:
    return (
        any(same_folder(left, parent) for parent in (right, *right.parents))
        or any(same_folder(right, parent) for parent in left.parents)
    )


class WorkspaceRegistry:
    def __init__(self, state_dir: Path) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        self.path = state_dir / "workspaces.sqlite3"
        self.protected_folders = [(state_dir, "Phyzflow application data")]
        sdk = Path(__file__).resolve().parent
        checkout = sdk.parents[2]
        if sdk == checkout / "workspace/src/workspace_sdk" and (checkout / "docs/application-spec.md").is_file():
            self.protected_folders.append((checkout, "the Phyzflow source checkout"))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE)")

    def entries(self) -> list[dict[str, str]]:
        with closing(sqlite3.connect(self.path)) as db:
            return [
                {"id": workspace_id, "path": path, "name": Path(path).name}
                for workspace_id, path in db.execute("SELECT id, path FROM workspaces ORDER BY rowid")
            ]

    def get(self, workspace_id: str) -> Path | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT path FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
            return Path(row[0]) if row else None

    def register(self, root: Path, action: object) -> str:
        if action not in ("create", "add"):
            raise ValueError("Choose whether to create or add a Workspace.")
        for folder, purpose in self.protected_folders:
            if folders_overlap(root, folder):
                raise ValueError(f"This folder overlaps {purpose}. Choose a separate project folder.")
        workspace_id = uuid4().hex
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for (path,) in db.execute("SELECT path FROM workspaces"):
                registered = Path(path)
                if same_folder(root, registered):
                    raise FileExistsError("This Workspace folder is already registered.")
                if folders_overlap(root, registered):
                    raise ValueError(f"This folder overlaps a registered Workspace at {registered}.")
            db.execute("INSERT INTO workspaces (id, path) VALUES (?, ?)", (workspace_id, str(root)))
            if action == "create":
                create_workspace(root)
            else:
                add_workspace(root)
        return workspace_id


def requested_root(value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Enter a folder path on the Phyzflow host.")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError("Enter an absolute folder path on the Phyzflow host.")
    return path.resolve()


def create_workspace(root: Path) -> None:
    try:
        root.mkdir()
    except FileExistsError:
        raise FileExistsError("Choose a new folder that does not already exist.") from None
    try:
        ensure_workspace_environment(root)
        try:
            initialize_workspace(root, with_main=True)
        except (OSError, ValueError):
            with suppress(OSError):
                rmtree(root / ".venv")
            raise
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        with suppress(OSError):
            root.rmdir()
        raise


def add_workspace(root: Path) -> None:
    if not root.is_dir():
        raise ValueError("Choose an existing project folder.")
    ensure_workspace_environment(root)


def serve_application(state_dir: Path, port: int) -> None:
    administrator = Administrator(state_dir)
    registry = WorkspaceRegistry(state_dir)
    tokens = McpTokens(administrator.directory)
    mounted = {}
    lock = Lock()
    shutting_down = False

    def mount_workspace(workspace_id: str, root: Path):
        with lock:
            if shutting_down:
                raise RuntimeError("The application is shutting down.")
            if workspace_id not in mounted:
                mounted[workspace_id] = workspace_routes(
                    root, discover_flows(root), load_ui_settings(root, port),
                    interpreter=workspace_interpreter(root),
                )
            return mounted[workspace_id]

    access = WorkspaceAccess(tokens, registry, lambda workspace_id, root: mount_workspace(workspace_id, root)[2])

    class Handler(UIRequestHandler):
        def end_headers(self) -> None:
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            super().end_headers()

        def request_origin(self) -> str:
            return f"http://{self.headers['Host']}"

        def valid_post_origin(self) -> bool:
            return self.local_request() and self.headers.get_all("Origin") == [self.request_origin()]

        def redirect(self, path: str, *, cookie: str | None = None) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", path)
            self.send_header("Cache-Control", "no-store")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()

        def sign_in_page(self, *, invalid: bool = False) -> None:
            page = (STATIC / "sign-in.html").read_bytes()
            if invalid:
                page = page.replace(b'role="alert" hidden', b'role="alert"')
            self.send_response(HTTPStatus.UNAUTHORIZED if invalid else HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(page)

        def sign_in(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/x-www-form-urlencoded" or not 0 < size <= 8192:
                    raise ValueError
                fields = parse_qs(self.rfile.read(size).decode(), strict_parsing=True, max_num_fields=2)
            except ValueError:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            cookie = administrator.sign_in(fields.get("username", [""])[0], fields.get("password", [""])[0])
            if cookie is None:
                self.sign_in_page(invalid=True)
            else:
                self.redirect("/", cookie=cookie)

        def authenticated(self) -> bool:
            return administrator.authenticated(self.headers.get("Cookie", ""))

        def workspace(self, route: re.Match) -> None:
            workspace_id, path = route.groups()
            try:
                root = registry.get(workspace_id)
                if root is None or not root.is_dir():
                    self.send_json({"error": "This Workspace folder is unavailable."}, HTTPStatus.NOT_FOUND)
                    return
                dispatch, _, _ = mount_workspace(workspace_id, root)
            except RuntimeError as error:
                self.send_json({"error": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            except (OSError, ValueError, sqlite3.Error) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            if path is None:
                self.send_response(HTTPStatus.PERMANENT_REDIRECT)
                self.send_header("Location", f"/workspaces/{workspace_id}/")
                self.end_headers()
                return
            dispatch(self, path)

        def mcp_request(self) -> None:
            if (
                not self.local_request() or len(self.headers.get_all("Host", [])) != 1
                or self.headers.get_all("Origin") not in (None, [self.request_origin()])
            ):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            identity = tokens.authenticate(self.headers.get_all("Authorization", []))
            if identity is None:
                self.send_json({"error": "A valid MCP bearer token is required."}, HTTPStatus.UNAUTHORIZED)
                return
            lengths = self.headers.get_all("Content-Length", [])
            try:
                size = int(lengths[0]) if lengths else 0
                if len(lengths) > 1 or "Transfer-Encoding" in self.headers or size < 0:
                    raise ValueError
            except ValueError:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            if size > MCP_BODY_LIMIT:
                self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            headers = [(name, value) for name, value in self.headers.items() if name.lower() in MCP_REQUEST_HEADERS]
            result = send_mcp(identity, self.command, headers, self.rfile.read(size))
            self.send_response(result.status_code)
            for name, value in result.headers.items():
                if name in MCP_RESPONSE_HEADERS:
                    self.send_header(name, value)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(result.content)))
            self.end_headers()
            self.wfile.write(result.content)

        def token_list(self) -> None:
            try:
                self.send_json({"tokens": tokens.entries(), "workspaces": registry.entries()})
            except sqlite3.Error:
                self.send_json({"error": "Could not read MCP access settings."}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def change_token(self, identity: str | None) -> None:
            try:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Send a JSON object with the MCP access settings.")
                body = self.read_json_dict(64 * 1024)
                if identity is not None and body.get("action") == "revoke":
                    tokens.revoke(identity)
                    self.send_json({})
                    return
                selected = body.get("workspace_ids")
                registered = {entry["id"] for entry in registry.entries()}
                if not isinstance(selected, list) or any(type(item) is not str or item not in registered for item in selected):
                    raise ValueError("Select registered Workspaces.")
                selected = list(dict.fromkeys(selected))
                if identity is None:
                    self.send_json(tokens.issue(body.get("name"), selected), HTTPStatus.CREATED)
                else:
                    tokens.reassign(identity, selected)
                    self.send_json({})
            except ValueError as error:
                self.send_json({"error": str(error) or "Send valid MCP access settings."}, HTTPStatus.BAD_REQUEST)
            except KeyError:
                self.send_json({"error": "This MCP token is unavailable."}, HTTPStatus.NOT_FOUND)
            except sqlite3.Error:
                self.send_json({"error": "Could not update MCP access settings."}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_GET(self) -> None:
            if self.path == "/mcp":
                self.mcp_request()
                return
            if not self.local_request():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if self.path == "/login":
                self.sign_in_page()
                return
            if self.path != "/workspace.css" and not self.authenticated():
                if "text/html" in self.headers.get("Accept", ""):
                    self.redirect("/login")
                else:
                    self.send_json({"error": "Sign in to Phyzflow."}, HTTPStatus.UNAUTHORIZED)
                return
            if route := WORKSPACE_PATH.fullmatch(self.path):
                self.workspace(route)
            elif self.path == "/workspaces":
                try:
                    entries = registry.entries()
                except sqlite3.Error:
                    self.send_json({"error": "Could not read the Workspace registry."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                else:
                    self.send_json({"workspaces": entries})
            elif self.path == "/mcp-tokens":
                self.token_list()
            elif self.path in ASSETS:
                asset, content_type = ASSETS[self.path]
                self.send_bytes((STATIC / asset).read_bytes(), f"{content_type}; charset=utf-8")
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path == "/mcp":
                self.mcp_request()
                return
            if not self.valid_post_origin():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if self.path == "/login":
                self.sign_in()
                return
            if not self.authenticated():
                self.send_json({"error": "Sign in to Phyzflow."}, HTTPStatus.UNAUTHORIZED)
                return
            if route := WORKSPACE_PATH.fullmatch(self.path):
                self.workspace(route)
                return
            if route := TOKEN_PATH.fullmatch(self.path):
                self.change_token(route[1])
                return
            if self.path != "/workspaces":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Send a JSON object with the folder path.")
                body = self.read_json_dict(8192)
                root = requested_root(body.get("path"))
                workspace_id = registry.register(root, body.get("action", "create"))
            except FileExistsError as error:
                self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, sqlite3.Error) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            else:
                self.send_json({"id": workspace_id}, HTTPStatus.CREATED)

        def do_DELETE(self) -> None:
            if self.path == "/mcp":
                self.mcp_request()
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

    try:
        with mcp_transport(access) as send_mcp, ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
            print(f"Phyzflow: {ui_address('127.0.0.1', server.server_port)}", flush=True)
            if administrator.initial_password.is_file():
                print(f"Sign in as admin with the initial password in {administrator.initial_password}.", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        with lock:
            shutting_down = True
            for _, close, _ in mounted.values():
                close()
