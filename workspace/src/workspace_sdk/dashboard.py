from pathlib import Path
import re
from urllib.parse import quote, unquote


PANELS_ROUTE = r"dashboard-panels/(workflow|[0-9]{1,8})"
ASSET_ROUTE = r"dashboard-assets/(workflow|[0-9]{1,8})(?:/([A-Za-z0-9._-]+))?"
SETTINGS_ROUTE = r"dashboard-settings/(workflow|[0-9]{1,8})"
PANEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
ASSET_NAMES = ("index.html", "style.css", "setting.js", "dashboard.js")
MAX_ASSET_BYTES = 256 * 1024

DASHBOARD_RESIZE_GUIDANCE = (
    "Design every dashboard panel to respond to its own available width and height. "
    "As the user resizes the panel, reflow the layout, resize charts, and keep primary values and controls visible. "
    "Let secondary details scroll when needed. "
    "Verify the panel by dragging its resize handles in Phyzflow while keeping the browser window size fixed."
)

DASHBOARD_THEME_REFERENCE = {
    "shared_stylesheet_injected": True,
    "style_attribute": 'data-phyzflow-theme="true"',
    "tokens": {
        "color": [
            "--bg",
            "--surface",
            "--panel",
            "--dock",
            "--line",
            "--ink",
            "--muted",
            "--accent",
            "--series-1",
            "--series-2",
            "--series-3",
            "--series-4",
            "--state-idle",
            "--state-live",
            "--state-running",
            "--state-succeeded",
            "--state-failed",
            "--state-stopped",
        ],
        "typography": ["--font-sans", "--mono"],
        "spacing": ["--space-1", "--space-2", "--space-3", "--space-4", "--radius-1", "--radius-2"],
    },
    "states": ["idle", "live", "running", "succeeded", "failed", "error", "empty", "stopped"],
    "classes": [
        "reading",
        "metric-grid",
        "metric-card",
        "metric-label",
        "metric-value",
        "state-badge",
        "state-banner",
        "chart-legend",
        "muted",
    ],
    "narrow_breakpoint_px": 420,
    "resize_guidance": DASHBOARD_RESIZE_GUIDANCE,
    "bridge": {
        "global": "window.workspaceDashboard",
        "methods": [
            "subscribe(listener)",
            "openNode(nodeId)",
            "openPanel(panelId, nodeId)",
            "setSetting(nodeId, name, value, mode)",
        ],
        "snapshot_fields": [
            "scope",
            "nodeId",
            "panelId",
            "mode",
            "graph",
            "run",
            "previews",
            "liveFrames",
            "liveMetrics",
            "settings",
            "settingChanges",
        ],
    },
}


def _scope_directory(root: Path, step_id: str | None) -> Path:
    directory = root / "dashboards"
    if step_id is None:
        return directory / "workflow"
    name = quote(step_id, safe=" ")
    if name in (".", ".."):
        name = name.replace(".", "%2E")
    return directory / "nodes" / name


def dashboard_panels(root: Path, step_id: str | None) -> list[dict[str, str]]:
    resolved_root = root.resolve()
    return [
        {"id": entry["id"], "label": entry["label"]}
        for entry in _scope_panel_entries(resolved_root, _scope_directory(resolved_root, step_id))
        if entry["ready"]
    ]


def dashboard_assets(root: Path, step_id: str | None, panel_id: str | None = None) -> dict[str, str]:
    directory = _scope_directory(root, step_id)
    if panel_id is not None and panel_id != "default":
        if panel_id in (".", "..") or not PANEL_ID_PATTERN.fullmatch(panel_id):
            raise PermissionError("Invalid dashboard panel identifier.")
        directory = directory / "panels" / panel_id

    assets = {}
    for name in ASSET_NAMES:
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


def _regular_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink() and path.resolve() == path


def _regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.resolve() == path


def _panel_entry(root: Path, directory: Path, panel_id: str, label: str) -> dict | None:
    if not _regular_dir(directory):
        return None
    assets = [
        (directory / name).relative_to(root).as_posix()
        for name in ASSET_NAMES
        if _regular_file(directory / name)
    ]
    if not assets:
        return None
    return {
        "id": panel_id,
        "label": label,
        "directory": directory.relative_to(root).as_posix(),
        "assets": assets,
        "ready": _regular_file(directory / "index.html"),
    }


def _scope_panel_entries(root: Path, base: Path) -> list[dict]:
    if not _regular_dir(base):
        return []
    panels: list[dict] = []
    default_entry = _panel_entry(root, base, "default", "Overview")
    if default_entry is not None:
        panels.append(default_entry)
    panels_dir = base / "panels"
    if _regular_dir(panels_dir):
        for sub in sorted(panels_dir.iterdir(), key=lambda p: p.name):
            if sub.name in (".", "..") or not PANEL_ID_PATTERN.fullmatch(sub.name):
                continue
            label = sub.name.replace("-", " ").replace("_", " ").strip() or sub.name
            entry = _panel_entry(root, sub, sub.name, label)
            if entry is not None:
                panels.append(entry)
    return panels


def inspect_sheet_dashboards(root: Path) -> dict:
    resolved_root = root.resolve()
    dashboards_dir = resolved_root / "dashboards"
    workflow: list[dict] = []
    nodes: list[dict] = []
    if _regular_dir(dashboards_dir):
        workflow = _scope_panel_entries(resolved_root, dashboards_dir / "workflow")
        nodes_dir = dashboards_dir / "nodes"
        if _regular_dir(nodes_dir):
            for sub in sorted(nodes_dir.iterdir(), key=lambda p: p.name):
                panels = _scope_panel_entries(resolved_root, sub)
                if panels:
                    nodes.append({
                        "step_id": unquote(sub.name),
                        "directory": sub.relative_to(resolved_root).as_posix(),
                        "panels": panels,
                    })
    return {
        "workflow": workflow,
        "nodes": nodes,
        "layout": {
            "workflow_directory": "dashboards/workflow",
            "node_directory_template": "dashboards/nodes/<step_id>",
            "panel_directory_template": "<scope_dir>/panels/<panel_id>",
            "files": list(ASSET_NAMES),
        },
        "theme_reference": DASHBOARD_THEME_REFERENCE,
    }
