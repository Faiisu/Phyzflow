from pathlib import Path
import re
from urllib.parse import quote, unquote


PANELS_ROUTE = r"dashboard-panels/(workflow|[0-9]{1,8})"
ASSET_ROUTE = r"dashboard-assets/(workflow|[0-9]{1,8})(?:/([A-Za-z0-9._-]+))?"
SETTINGS_ROUTE = r"dashboard-settings/(workflow|[0-9]{1,8})"
PANEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
DEFAULT_PANEL_ID = "default"
ASSET_NAMES = ("index.html", "style.css", "setting.js", "dashboard.js", "bridge.json")
WORKSPACE_DASHBOARD_DIRECTORY = Path("dashboards/workspace")
DASHBOARD_BRIDGE_VERSION = 1
MAX_ASSET_BYTES = 256 * 1024
MAX_WORKSPACE_FRAME_SOURCES = 4

DASHBOARD_AUTHORING_GUIDANCE = (
    "Use the connected Phyzflow MCP tools to inspect, edit, and verify dashboard files. "
    "Find the Workspace ID with list_workspaces if needed. For a Workspace Dashboard, call "
    "inspect_dashboards with sheet: null. Use the returned Workspace-relative paths with "
    "read_workspace_text and write_workspace_text, passing the read revision or 'missing' for a new file. "
    "For a Workflow or Node Dashboard, pass the named sheet to inspect_dashboards and use read_file and write_file. "
    "Read the saved files back and call inspect_dashboards again to verify panel discovery. "
    "Dashboard-only edits do not require running or inspecting unrelated flows. "
    "Do not search for a web UI URL or open a browser during MCP dashboard authoring. "
    "Use a browser for visual checks only if the user explicitly asks for them."
)

DASHBOARD_RESIZE_GUIDANCE = (
    "Design every dashboard panel to respond to its own available width and height. "
    "As the user resizes the panel, reflow the layout, resize charts, and keep primary values and controls visible. "
    "Let secondary details scroll when needed. "
    "If the user explicitly requests browser verification, drag the panel's resize handles in Phyzflow "
    "while keeping the browser window size fixed."
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
        "version": DASHBOARD_BRIDGE_VERSION,
        "declaration": {"file": "bridge.json", "example": {"versions": [DASHBOARD_BRIDGE_VERSION]}},
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


def _additional_panel_id(panel_id: str) -> bool:
    return panel_id not in (".", "..", DEFAULT_PANEL_ID) and bool(PANEL_ID_PATTERN.fullmatch(panel_id))


def dashboard_directory(root: Path, step_id: str | None, panel_id: str | None, *, workspace: bool) -> Path:
    directory = root / WORKSPACE_DASHBOARD_DIRECTORY if workspace else _scope_directory(root, step_id)
    if panel_id is not None and panel_id != DEFAULT_PANEL_ID:
        if not _additional_panel_id(panel_id):
            raise PermissionError("Invalid dashboard panel identifier.")
        directory = directory / "panels" / panel_id
    return directory


def dashboard_panels(root: Path, step_id: str | None, *, workspace: bool = False) -> list[dict[str, str]]:
    resolved_root = root.resolve()
    directory = dashboard_directory(resolved_root, step_id, None, workspace=workspace)
    return [
        {"id": entry["id"], "label": entry["label"]}
        for entry in _scope_panel_entries(resolved_root, directory)
        if entry["ready"]
    ]


def dashboard_assets(
    root: Path, step_id: str | None, panel_id: str | None = None, *, workspace: bool = False,
    manifest_only: bool = False,
) -> dict[str, str]:
    directory = dashboard_directory(root, step_id, panel_id, workspace=workspace)
    if manifest_only and not _regular_file(directory / "index.html"):
        raise FileNotFoundError("index.html")
    assets = {}
    for name in ("bridge.json",) if manifest_only else ASSET_NAMES:
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


def dashboard_panel_label(panel_id: str) -> str:
    if panel_id == DEFAULT_PANEL_ID:
        return "Overview"
    return panel_id.replace("-", " ").replace("_", " ").strip() or panel_id


def _panel_entry(root: Path, directory: Path, panel_id: str) -> dict | None:
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
        "label": dashboard_panel_label(panel_id),
        "directory": directory.relative_to(root).as_posix(),
        "assets": assets,
        "ready": _regular_file(directory / "index.html"),
    }


def _scope_panel_entries(root: Path, base: Path) -> list[dict]:
    if not _regular_dir(base):
        return []
    panels: list[dict] = []
    default_entry = _panel_entry(root, base, DEFAULT_PANEL_ID)
    if default_entry is not None:
        panels.append(default_entry)
    panels_dir = base / "panels"
    if _regular_dir(panels_dir):
        for sub in sorted(panels_dir.iterdir(), key=lambda p: p.name):
            if not _additional_panel_id(sub.name):
                continue
            entry = _panel_entry(root, sub, sub.name)
            if entry is not None:
                panels.append(entry)
    return panels


def sheet_panel_catalog(
    root: Path, sheets: list[tuple[str, Path]], node_ids: dict[Path, set[str] | None],
) -> list[dict]:
    panels = []
    for name, sheet in sheets:
        details = inspect_sheet_dashboards(sheet)
        sheet_path = sheet.relative_to(root).as_posix()
        scopes = [{"step_id": None, "panels": details["workflow"]}, *details["nodes"]]
        step_ids = node_ids.get(sheet)
        for scope in scopes:
            if scope["step_id"] is not None and step_ids is not None and scope["step_id"] not in step_ids:
                continue
            panels.extend({
                "sheet": sheet_path,
                "sheet_name": name,
                "scope": scope["step_id"],
                "id": entry["id"],
                "label": entry["label"],
            } for entry in scope["panels"] if entry["ready"])
    return panels


def inspect_sheet_dashboards(root: Path) -> dict:
    resolved_root = root.resolve()
    dashboards_dir = resolved_root / "dashboards"
    workflow: list[dict] = []
    nodes: list[dict] = []
    if _regular_dir(dashboards_dir):
        workflow = _scope_panel_entries(resolved_root, _scope_directory(resolved_root, None))
        nodes_dir = dashboards_dir / "nodes"
        if _regular_dir(nodes_dir):
            for sub in sorted(nodes_dir.iterdir(), key=lambda p: p.name):
                if not sub.name.isascii():
                    continue
                step_id = unquote(sub.name)
                if sub != _scope_directory(resolved_root, step_id):
                    continue
                panels = _scope_panel_entries(resolved_root, sub)
                if panels:
                    nodes.append({
                        "step_id": step_id,
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


def inspect_workspace_dashboards(root: Path) -> dict:
    root = root.resolve()
    return {
        "panels": _scope_panel_entries(root, root / WORKSPACE_DASHBOARD_DIRECTORY),
        "directory": WORKSPACE_DASHBOARD_DIRECTORY.as_posix(),
        "panel_directory_template": "dashboards/workspace/panels/<panel_id>",
        "bridge": {
            "version": DASHBOARD_BRIDGE_VERSION,
            "methods": ["subscribe(listener)"],
            "snapshot_fields": ["scope", "sources"],
            "frame_sources": {
                "mode": "live",
                "max_per_panel": MAX_WORKSPACE_FRAME_SOURCES,
                "value": "JPEG data URL from the selected step's latest publish_frame call",
            },
            "declaration_example": {
                "versions": [DASHBOARD_BRIDGE_VERSION],
                "sources": [
                    {"id": "main-duration", "sheet": ".", "field": "duration_ms"},
                    {"id": "camera", "sheet": "flows/Vision AI", "mode": "live", "frame": {"step": "Camera"}},
                ],
            },
        },
    }
