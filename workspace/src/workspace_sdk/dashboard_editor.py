from http import HTTPStatus
import json
from pathlib import Path
from shutil import rmtree
from tempfile import TemporaryDirectory, mkdtemp
from threading import Lock

from .dashboard import ASSET_NAMES, DASHBOARD_BRIDGE_VERSION, MAX_ASSET_BYTES, dashboard_directory
from .node_sources import edit_source_file
from .sheet_locks import SheetEditLease, SheetLockedError


SOURCE_ROUTE = r"dashboard-source/(workflow|[0-9]{1,8})"
_edit_lock = Lock()
STARTER_HTML = """<main class="reading">
  <h1>Dashboard</h1>
  <p class="muted" role="status">Waiting for data…</p>
</main>
"""
STARTER_JS = """const status = document.querySelector('[role="status"]');

workspaceDashboard.subscribe(({run, sources}) => {
  status.textContent = sources?.[0]?.value ?? run?.status ?? "Waiting for data…";
});
"""


def _check_path(path: Path, *, directory: bool = False) -> None:
    if path.resolve() != path or (path.exists() and not (path.is_dir() if directory else path.is_file())):
        raise PermissionError


def _create_panel(directory: Path, *, workspace: bool) -> None:
    if directory.exists():
        raise FileExistsError
    directory.parent.mkdir(parents=True, exist_ok=True)
    declaration = {"versions": [DASHBOARD_BRIDGE_VERSION]}
    if workspace:
        declaration["sources"] = [{"id": "main-status", "sheet": ".", "field": "status", "mode": "live"}]
    with TemporaryDirectory(dir=directory.parent.parent, prefix=".dashboard-create-") as temp:
        staged = Path(temp) / "panel"
        staged.mkdir()
        (staged / "index.html").write_text(STARTER_HTML, encoding="utf-8")
        (staged / "style.css").write_text("main { padding: var(--space-4); }\n", encoding="utf-8")
        (staged / "dashboard.js").write_text(STARTER_JS, encoding="utf-8")
        (staged / "bridge.json").write_text(json.dumps(declaration, indent=2) + "\n", encoding="utf-8")
        if directory.exists():
            raise FileExistsError
        staged.rename(directory)


def _remove_panel(directory: Path) -> None:
    paths = [directory / name for name in ASSET_NAMES]
    for path in paths:
        _check_path(path)
    existing = [path for path in paths if path.is_file()]
    if not existing:
        raise FileNotFoundError
    backup = Path(mkdtemp(dir=directory, prefix=".dashboard-remove-"))
    moved = []
    try:
        for path in existing:
            path.rename(backup / path.name)
            moved.append(path)
    except OSError:
        for path in reversed(moved):
            (backup / path.name).rename(path)
        backup.rmdir()
        raise
    rmtree(backup)
    if directory.parent.name == "panels" and not any(directory.iterdir()):
        directory.rmdir()


def _edit(root: Path, step_id: str | None, data: dict, *, workspace: bool) -> dict:
    action = data.get("action")
    panel_id = data.get("panel")
    if not isinstance(panel_id, str) or not panel_id:
        raise ValueError("Send a dashboard panel identifier.")
    directory = dashboard_directory(root.resolve(), step_id, panel_id, workspace=workspace)
    with _edit_lock:
        _check_path(directory, directory=True)
        if action == "create":
            if panel_id == "default" or not panel_id.strip("._-"):
                raise ValueError("Use a panel name containing letters or numbers other than default.")
            _create_panel(directory, workspace=workspace)
            return {}
        if action == "remove":
            _remove_panel(directory)
            return {}
        if action not in ("read", "save") or data.get("name") not in ASSET_NAMES:
            raise ValueError("Choose a dashboard source file.")
        if not directory.is_dir():
            raise FileNotFoundError
        path = directory / data["name"]
        _check_path(path)
        if action == "read":
            return edit_source_file(path, None, None, initial="", max_bytes=MAX_ASSET_BYTES)
        source, revision = data.get("source"), data.get("revision")
        if not isinstance(source, str) or not isinstance(revision, str):
            raise ValueError("Send the source and file revision.")
        return edit_source_file(path, source, revision, initial="", max_bytes=MAX_ASSET_BYTES)


def send_dashboard_edit(
    request, root: Path, step_id: str | None, data: dict, *,
    workspace: bool = False, sheet_lease: SheetEditLease | None = None,
) -> None:
    def perform():
        try:
            return _edit(root, step_id, data, workspace=workspace)
        except PermissionError:
            raise ValueError("Choose a dashboard panel and regular source files inside its folder.") from None
        except FileExistsError:
            raise ValueError("A dashboard with that panel ID already exists.") from None

    def edit_with_lease():
        if sheet_lease is None or data.get("action") not in ("create", "save", "remove"):
            return perform()
        session_id = request.headers.get("X-Phyzflow-Edit-Session")

        def mutate():
            if session_id is None:
                raise SheetLockedError("Open the sheet for editing first.", snapshot=sheet_lease.status())
            return perform()

        return sheet_lease.write_locked("web", session_id, mutate)

    request.send_source_edit(edit_with_lease, not_found_error="This dashboard panel is unavailable.")
