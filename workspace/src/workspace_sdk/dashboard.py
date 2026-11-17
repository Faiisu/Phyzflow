from pathlib import Path
import re
from urllib.parse import quote


PANELS_ROUTE = r"dashboard-panels/(workflow|[0-9]{1,8})"
ASSET_ROUTE = r"dashboard-assets/(workflow|[0-9]{1,8})(?:/([A-Za-z0-9._-]+))?"
SETTINGS_ROUTE = r"dashboard-settings/(workflow|[0-9]{1,8})"
PANEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_ASSET_BYTES = 256 * 1024


def _scope_directory(root: Path, step_id: str | None) -> Path:
    directory = root / "dashboards"
    if step_id is None:
        return directory / "workflow"
    name = quote(step_id, safe=" ")
    if name in (".", ".."):
        name = name.replace(".", "%2E")
    return directory / "nodes" / name


def dashboard_panels(root: Path, step_id: str | None) -> list[dict[str, str]]:
    base = _scope_directory(root, step_id)
    panels: list[dict[str, str]] = []
    root_index = base / "index.html"
    if root_index.resolve() == root_index and root_index.is_file():
        panels.append({"id": "default", "label": "Overview"})
    panels_dir = base / "panels"
    if panels_dir.is_dir() and panels_dir.resolve() == panels_dir:
        for sub in sorted(panels_dir.iterdir(), key=lambda p: p.name):
            if not sub.is_dir() or sub.resolve() != sub:
                continue
            if sub.name in (".", "..") or not PANEL_ID_PATTERN.fullmatch(sub.name):
                continue
            index_file = sub / "index.html"
            if index_file.resolve() == index_file and index_file.is_file():
                label = sub.name.replace("-", " ").replace("_", " ").strip() or sub.name
                panels.append({"id": sub.name, "label": label})
    return panels


def dashboard_assets(root: Path, step_id: str | None, panel_id: str | None = None) -> dict[str, str]:
    directory = _scope_directory(root, step_id)
    if panel_id is not None and panel_id != "default":
        if panel_id in (".", "..") or not PANEL_ID_PATTERN.fullmatch(panel_id):
            raise PermissionError("Invalid dashboard panel identifier.")
        directory = directory / "panels" / panel_id

    assets = {}
    for name in ("index.html", "style.css", "setting.js", "dashboard.js"):
        path = directory / name
        # Only load regular files under the workflow's dashboard directory.
        if path.resolve() != path:
            raise PermissionError("Dashboard assets must be regular files without symbolic links.")
        if not path.is_file():
            if name == "index.html":
                raise FileNotFoundError(name)
            continue
        with path.open("rb") as stream:
            content = stream.read(MAX_ASSET_BYTES + 1)
        if len(content) > MAX_ASSET_BYTES:
            raise ValueError(f"{name} exceeds 256 KiB.")
        assets[name] = content.decode("utf-8")
    return assets
