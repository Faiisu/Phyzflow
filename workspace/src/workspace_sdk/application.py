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
from uuid import uuid4

from .sheets import discover_flows, initialize_workspace
from .ui_settings import load_ui_settings
from .web import STATIC, UIRequestHandler, ui_address
from .workspace_environment import ensure_workspace_environment, workspace_interpreter
from .workspace_ui import workspace_routes


WORKSPACE_PATH = re.compile(r"/workspaces/([0-9a-f]{32})(/.*)?")
ASSETS = {
    "/": ("landing.html", "text/html"),
    "/landing.js": ("landing.js", "text/javascript"),
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
    registry = WorkspaceRegistry(state_dir)
    mounted = {}
    lock = Lock()
    shutting_down = False

    class Handler(UIRequestHandler):
        def workspace(self, route: re.Match) -> None:
            workspace_id, path = route.groups()
            try:
                with lock:
                    if shutting_down:
                        self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
                        return
                    root = registry.get(workspace_id)
                    if root is None or not root.is_dir():
                        self.send_json({"error": "This Workspace folder is unavailable."}, HTTPStatus.NOT_FOUND)
                        return
                    if workspace_id not in mounted:
                        mounted[workspace_id] = workspace_routes(
                            root, discover_flows(root), load_ui_settings(root, port),
                            interpreter=workspace_interpreter(root),
                        )
                    dispatch, _, _ = mounted[workspace_id]
            except (OSError, ValueError, sqlite3.Error) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            if path is None:
                self.send_response(HTTPStatus.PERMANENT_REDIRECT)
                self.send_header("Location", f"/workspaces/{workspace_id}/")
                self.end_headers()
                return
            dispatch(self, path)

        def do_GET(self) -> None:
            if not self.local_request():
                self.send_error(HTTPStatus.FORBIDDEN)
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
            elif self.path in ASSETS:
                asset, content_type = ASSETS[self.path]
                self.send_bytes((STATIC / asset).read_bytes(), f"{content_type}; charset=utf-8")
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if not self.valid_post_origin():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if route := WORKSPACE_PATH.fullmatch(self.path):
                self.workspace(route)
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

    try:
        with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
            print(f"Phyzflow: {ui_address('127.0.0.1', server.server_port)}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        with lock:
            shutting_down = True
            for _, close, _ in mounted.values():
                close()
