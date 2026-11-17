from pathlib import Path
from urllib.parse import quote


ASSET_ROUTE = r"dashboard-assets/(workflow|[0-9]{1,8})"
SETTINGS_ROUTE = r"dashboard-settings/(workflow|[0-9]{1,8})"
MAX_ASSET_BYTES = 256 * 1024


def dashboard_assets(root: Path, step_id: str | None) -> dict[str, str]:
    directory = root / "dashboards"
    if step_id is None:
        directory /= "workflow"
    else:
        name = quote(step_id, safe=" ")
        if name in (".", ".."):
            name = name.replace(".", "%2E")
        directory = directory / "nodes" / name

    assets = {}
    for name in ("index.html", "style.css", "dashboard.js"):
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
