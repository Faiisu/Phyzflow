"""Discover and create the Python projects represented by Workspace sheets."""

from importlib.metadata import version
import json
from pathlib import Path
from unicodedata import category, normalize

from .ui_settings import load_ui_settings


STARTER_FLOW = '''from workspace_sdk import Flow, Output, Step


def start() -> dict[str, str]:
    return {"message": "Hello from Workspace"}


flow = Flow(
    inputs=(),
    steps=(Step("Start", start, {}),),
    output=Output("Start"),
)
'''


def discover_flows(root: Path) -> list[tuple[str, Path]]:
    folder = root / "flows"
    flows = []
    if folder.is_dir():
        flows = [(path.name, path) for path in sorted(folder.iterdir()) if (path / "flow.py").is_file()]
    if (root / "flow.py").is_file():
        flows.insert(0, ("Main", root))
    return flows


def create_sheet(root: Path, value: object) -> Path:
    name = normalize("NFC", value.strip()) if isinstance(value, str) else ""
    if not (
        0 < len(name) <= 64 and name[0].isalnum()
        and all(char.isalnum() or char in " _-" or category(char).startswith("M") for char in name)
    ):
        raise ValueError("Start with a letter or number and use up to 64 letters, numbers, spaces, underscores, or hyphens.")
    parent = root / "flows"
    if parent.is_symlink():
        raise ValueError("The flows directory must be inside this Workspace.")
    parent.mkdir(exist_ok=True)
    if name.casefold() == "main" or any(
        normalize("NFC", path.name).casefold() == name.casefold() for path in parent.iterdir()
    ):
        raise FileExistsError("That sheet name is already in use. Choose another name.")
    directory = parent / name
    try:
        directory.mkdir()
    except FileExistsError:
        raise FileExistsError("That sheet name is already in use. Choose another name.") from None

    pending = directory / ".flow.py.tmp"
    entrypoint = directory / "flow.py"
    try:
        pending.write_text(STARTER_FLOW, encoding="utf-8")
        pending.rename(entrypoint)
    except OSError:
        pending.unlink(missing_ok=True)
        directory.rmdir()
        raise
    return entrypoint


def initialize_workspace(root: Path) -> None:
    settings = load_ui_settings(root, None)
    project = (
        '[project]\n'
        'name = "workspace-project"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.12"\n'
        f'dependencies = ["workspace-sdk=={version("workspace-sdk")}"]\n'
    )
    root.mkdir(parents=True, exist_ok=True)
    if any(path.name != ".DS_Store" for path in root.iterdir()):
        raise ValueError(f"Choose an empty folder to initialize a Workspace: {root}")
    files = {
        "setting.json": json.dumps({"port": settings.port, "zoom": settings.zoom}, indent=2) + "\n",
        "pyproject.toml": project,
    }
    created = []
    try:
        for name, content in files.items():
            path = root / name
            with path.open("x", encoding="utf-8") as stream:
                created.append(path)
                stream.write(content)
    except OSError:
        for path in created:
            path.unlink()
        raise
