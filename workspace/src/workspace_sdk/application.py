"""Host the landing page and registered Workspace routes on one origin."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, suppress
from dataclasses import asdict
import errno
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, distribution
import json
import os
from pathlib import Path
import re
import select
import shutil
from shutil import rmtree
import signal
import socket
import sqlite3
import subprocess
import sys
from threading import Event, Lock, Thread, current_thread, main_thread
from urllib.parse import parse_qs, urlsplit
from urllib.request import url2pathname
from uuid import uuid4

from .application_auth import Administrator
from .application_mcp import (
    MCP_BODY_LIMIT, MCP_REQUEST_HEADERS, MCP_RESPONSE_HEADERS, TOKEN_PATH,
    McpTokens, WorkspaceAccess, mcp_transport,
)
from .application_mcp.file_transfers import FileTransfers
from .project import workspace_format_notice
from .sheet_runs import RunStateStore
from .sheets import discover_flows, initialize_workspace
from .ui_settings import (
    application_network, load_application_network, load_ui_settings, save_application_network,
    validate_ipv4, validate_port,
)
from .web import STATIC, UIRequestHandler, ui_address
from .workspace_environment import (
    ensure_workspace_environment, folders_overlap, same_folder, verified_workspace_interpreter, workspace_interpreter,
)
from .workspace_ui import (
    WorkspaceProcessRunningError, verify_workspace_processes_stopped, workspace_routes,
)


WORKSPACE_PATH = re.compile(r"/workspaces/([0-9a-f]{32})(/.*)?")
WORKSPACE_DELETE_PATH = re.compile(r"/workspaces/([0-9a-f]{32})")
ASSETS = {
    "/": ("landing.html", "text/html"),
    "/landing.js": ("landing.js", "text/javascript"),
    "/mcp-access.js": ("mcp-access.js", "text/javascript"),
    "/settings/network": ("network-settings.html", "text/html"),
    "/network-settings.js": ("network-settings.js", "text/javascript"),
    "/workspace.css": ("workspace.css", "text/css"),
}


def _is_phyzflow_checkout(folder: Path) -> bool:
    return (folder / "docs/application-spec.md").is_file() and (folder / "workspace/src/workspace_sdk").is_dir()


def _find_phyzflow_checkout() -> Path | None:
    sdk = Path(__file__).resolve().parent
    if len(sdk.parents) >= 3:
        candidate = sdk.parents[2]
        if sdk == candidate / "workspace/src/workspace_sdk" and _is_phyzflow_checkout(candidate):
            return candidate
    with suppress(PackageNotFoundError, OSError, ValueError):
        raw = distribution("workspace-sdk").read_text("direct_url.json")
        if raw:
            url = json.loads(raw).get("url", "")
            if isinstance(url, str) and url.startswith("file://"):
                source = urlsplit(url)
                authority = f"//{source.netloc}" if source.netloc.lower() not in ("", "localhost") else ""
                source_dir = Path(url2pathname(authority + source.path)).resolve()
                checkout = source_dir.parent
                if source_dir.name == "workspace" and _is_phyzflow_checkout(checkout):
                    return checkout
    cwd = Path.cwd().resolve()
    for folder in (cwd, *cwd.parents):
        if _is_phyzflow_checkout(folder):
            return folder
    return None


class WorkspaceRegistry:
    def __init__(self, state_dir: Path) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        self.path = state_dir / "workspaces.sqlite3"
        self.protected_folders = [(state_dir.resolve(), "Phyzflow application data")]
        checkout = _find_phyzflow_checkout()
        if checkout is not None:
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

    def unregister(self, workspace_id: str) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))

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


def requested_root(value: object, *, preserve_whitespace: bool = False) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Enter a folder path on the Phyzflow host.")
    path = Path(value if preserve_whitespace else value.strip()).expanduser()
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
    workspace_format_notice(root)
    ensure_workspace_environment(root)


def move_workspace_to_trash(root: Path) -> Path | None:
    configured = os.environ.get("PHYZFLOW_TRASH_DIR")
    if configured:
        trash = Path(configured)
    elif os.name == "nt":
        from .windows_workspace import recycle_folder

        recycle_folder(root)
        return None
    elif sys.platform == "darwin":
        trash = Path.home() / ".Trash"
    else:
        trash = Path.home() / ".local/share/Trash/files"
    try:
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / root.name
        suffix = 2
        while target.exists() or target.is_symlink():
            target = trash / f"{root.name} {suffix}"
            suffix += 1
        try:
            root.rename(target)
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            shutil.move(str(root), str(target))
    except OSError as error:
        raise OSError(f"Could not move {root.name} to Trash: {error.strerror or error}") from error
    return target


def serve_application(state_dir: Path, port: int | None = None, *, ipv4: str | None = None) -> None:
    network_path = state_dir / "network.json"
    saved_network = load_application_network(network_path)
    ipv4 = saved_network.ipv4 if ipv4 is None else validate_ipv4(ipv4, "The bind IP")
    port = saved_network.port if port is None else validate_port(port, "The server port")
    administrator = Administrator(state_dir)
    registry = WorkspaceRegistry(state_dir)
    tokens = McpTokens(administrator.directory)
    run_state = RunStateStore(state_dir)
    mounted = {}
    deleting: set[str] = set()
    lock = Lock()
    shutting_down = False
    shutdown_event = Event()
    restore_thread: Thread | None = None

    def is_deleting(workspace_id: str) -> bool:
        with lock:
            return workspace_id in deleting

    def mounted_sheet_resolver(workspace_id: str):
        with lock:
            entry = mounted.get(workspace_id)
            return entry[2] if entry is not None else None

    def mount_workspace(workspace_id: str, root: Path):
        with access.file_gate, lock:
            if shutting_down:
                raise RuntimeError("The application is shutting down.")
            if workspace_id in deleting:
                raise RuntimeError("This Workspace is being deleted.")
            if workspace_id not in mounted:
                flows = discover_flows(root)
                interpreter = verified_workspace_interpreter(root) if os.name == "nt" else workspace_interpreter(root)
                mounted[workspace_id] = workspace_routes(
                    root, flows, load_ui_settings(root, port),
                    interpreter=interpreter,
                    workspace_id=workspace_id,
                    run_state=run_state,
                    protected_folders=access.protected_folders,
                    app_closing=shutdown_event.is_set,
                )
            return mounted[workspace_id]

    def restore_workspaces() -> None:
        if shutdown_event.is_set():
            return
        targets: list[tuple[str, Path]] = []
        for workspace_id in run_state.active_workspace_ids():
            root = registry.get(workspace_id)
            if root is not None and root.is_dir():
                targets.append((workspace_id, root))
        if not targets:
            return

        def restore_one(workspace_id: str, root: Path) -> None:
            if shutdown_event.is_set():
                return
            try:
                mount_workspace(workspace_id, root)
            except (RuntimeError, OSError, ValueError, sqlite3.Error):
                pass

        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            futures = [pool.submit(restore_one, workspace_id, root) for workspace_id, root in targets]
            for future in futures:
                future.result()

    access = WorkspaceAccess(
        tokens, registry, lambda workspace_id, root: mount_workspace(workspace_id, root)[2],
        is_deleting=is_deleting, mounted_sheet_resolver=mounted_sheet_resolver,
    )

    transfers = FileTransfers(access, administrator.directory / "mcp-file-staging")

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
                page = page.replace(b'role="alert" hidden', b'role="alert" autofocus')
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
            if is_deleting(workspace_id):
                self.send_json({"error": "This Workspace is being deleted."}, HTTPStatus.CONFLICT)
                return
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

            def client_disconnected() -> bool:
                try:
                    if not select.select([self.connection], [], [], 0)[0]:
                        return False
                    return self.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
                except OSError:
                    return True

            result = send_mcp(
                identity, self.command, headers, self.rfile.read(size),
                disconnected=client_disconnected,
            )
            if result is None:
                return
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

        def update_network_settings(self) -> None:
            try:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Send network settings as a JSON object.")
                body = self.read_json_dict(8192)
                if body.keys() != {"ipv4", "port"}:
                    raise ValueError("Provide the bind IPv4 address and server port.")
                settings = application_network(body)
            except ValueError as error:
                self.send_json({"error": str(error) or "Send a valid network settings object."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.bind((settings.ipv4, 0))
            except OSError:
                self.send_json({"error": "The bind IP is unavailable on this host."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                save_application_network(network_path, settings)
            except OSError:
                self.send_json({"error": "Could not save the network settings."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self.send_json({"saved": asdict(settings)})

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
            elif self.path == "/network-settings":
                try:
                    settings = load_application_network(network_path)
                except (OSError, ValueError):
                    self.send_json({"error": "Could not read the saved network settings."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                else:
                    address, listening_port = server.server_address
                    self.send_json({
                        "saved": asdict(settings),
                        "active": {"ipv4": address, "port": listening_port},
                    })
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
            if self.path == "/workspace-folders":
                self.browse_workspace_folders()
                return
            if self.path == "/network-settings":
                self.update_network_settings()
                return
            if self.path != "/workspaces":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Send a JSON object with the folder path.")
                body = self.read_json_dict(8192)
                action = body.get("action", "create")
                if action == "create" and "parent" in body:
                    name = body.get("name")
                    name = name.strip() if isinstance(name, str) else ""
                    if name in ("", ".", "..") or Path(name).name != name or "\\" in name or "\0" in name:
                        raise ValueError("Enter a single folder name.")
                    root = requested_root(body["parent"], preserve_whitespace=True) / name
                elif action == "add" and "folder" in body:
                    root = requested_root(body["folder"], preserve_whitespace=True)
                else:
                    root = requested_root(body.get("path"))
                workspace_id = registry.register(root, action)
            except FileExistsError as error:
                self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, sqlite3.Error) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            else:
                self.send_json({"id": workspace_id}, HTTPStatus.CREATED)

        def browse_workspace_folders(self) -> None:
            try:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError
                body = self.read_json_dict(8192)
                folder = (
                    requested_root(body["path"], preserve_whitespace=True)
                    if "path" in body else Path.home().resolve()
                )
                protected = [path for path, _ in registry.protected_folders]
                if any(same_folder(parent, path) for parent in (folder, *folder.parents) for path in protected):
                    raise PermissionError
                with os.scandir(folder) as entries:
                    folders = [
                        {"name": entry.name, "path": entry.path}
                        for entry in entries
                        if not entry.name.startswith(".")
                        and entry.is_dir(follow_symlinks=False)
                        and not any(same_folder(Path(entry.path), path) for path in protected)
                    ]
            except ValueError:
                self.send_json({"error": "Choose an absolute folder path on the Phyzflow host."}, HTTPStatus.BAD_REQUEST)
            except (OSError, RuntimeError):
                self.send_json({"error": "Could not open this folder on the Phyzflow host."}, HTTPStatus.BAD_REQUEST)
            else:
                self.send_json({
                    "path": str(folder),
                    "parent": str(folder.parent) if folder != folder.parent else None,
                    "folders": sorted(folders, key=lambda entry: (entry["name"].casefold(), entry["name"])),
                })

        def delete_workspace(self, workspace_id: str) -> None:
            try:
                with access.file_gate, lock:
                    if shutting_down:
                        self.send_json({"error": "The application is shutting down."}, HTTPStatus.SERVICE_UNAVAILABLE)
                        return
                    if workspace_id in deleting:
                        self.send_json({"error": "This Workspace is already being deleted."}, HTTPStatus.CONFLICT)
                        return
                    root = registry.get(workspace_id)
                    if root is None:
                        self.send_json({"error": "This Workspace is unavailable."}, HTTPStatus.NOT_FOUND)
                        return
                    deleting.add(workspace_id)
                    route_entry = mounted.get(workspace_id)
            except sqlite3.Error:
                self.send_json({"error": "Could not read the Workspace registry."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            try:
                try:
                    if route_entry is not None:
                        route_entry[1](delete=True)
                    else:
                        verify_workspace_processes_stopped(root, wait_seconds=0.2)
                except WorkspaceProcessRunningError as error:
                    self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
                    return
                try:
                    transfers.cancel_workspace(workspace_id)
                    move_workspace_to_trash(root)
                except OSError as error:
                    with lock:
                        mounted.pop(workspace_id, None)
                    self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                with lock:
                    mounted.pop(workspace_id, None)
                registry.unregister(workspace_id)
                run_state.delete_workspace(workspace_id)
                self.send_json({"status": "deleted"})
            except sqlite3.Error:
                self.send_json({"error": "Could not update the Workspace registry."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            finally:
                with lock:
                    deleting.discard(workspace_id)

        def do_DELETE(self) -> None:
            if self.path == "/mcp":
                self.mcp_request()
                return
            if not self.valid_post_origin():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if not self.authenticated():
                self.send_json({"error": "Sign in to Phyzflow."}, HTTPStatus.UNAUTHORIZED)
                return
            if route := WORKSPACE_DELETE_PATH.fullmatch(self.path):
                self.delete_workspace(route[1])
                return
            self.send_error(HTTPStatus.NOT_FOUND)

    try:
        with mcp_transport(access, transfers) as send_mcp, ThreadingHTTPServer((ipv4, port), Handler) as server:
            print(f"Phyzflow: {ui_address(ipv4, server.server_port)}", flush=True)
            if administrator.initial_password.is_file():
                print(f"Sign in as admin with the initial password in {administrator.initial_password}.", flush=True)
            restore_thread = Thread(target=restore_workspaces, daemon=True)
            restore_thread.start()

            def stop(*_) -> None:
                Thread(target=server.shutdown, daemon=True).start()

            previous = signal.signal(signal.SIGTERM, stop) if current_thread() is main_thread() else None
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                if previous is not None:
                    signal.signal(signal.SIGTERM, previous)
    finally:
        shutdown_event.set()
        with lock:
            shutting_down = True
        if restore_thread is not None:
            restore_thread.join()
        with lock:
            mounted_routes = list(mounted.values())
        for _, close, _ in mounted_routes:
            close()
        transfers.close()
        run_state.close()
