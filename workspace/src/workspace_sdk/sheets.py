"""Discover and create the Python projects represented by Workspace sheets."""

from collections.abc import Iterator
from contextlib import ExitStack
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
from typing import Callable
from unicodedata import category, normalize
from uuid import uuid4

from .node_sources import edit_source_file
from .ui_settings import load_ui_settings


STARTER_FLOW = '''from workspace_sdk import Flow, Output, Step

from nodes.start.entrypoint import start

flow = Flow(
    inputs=(),
    steps=(Step("Start", start, {}),),
    output=Output("Start"),
)
'''

STARTER_NODE = '''def start() -> dict[str, str]:
    return {"message": "Hello from Workspace"}
'''

STARTER_SOURCES = '[tool.workspace]\nsources = ["nodes"]\n'

WORKSPACE_AGENT_GUIDE = """# Workspace instructions

- Use one Python package per node under the sheet's `nodes/` directory.
- Put the node callable in `nodes/<node_name>/entrypoint.py` and keep its helpers in that package.
- Put shared default values for settings declared in `Step.settings` in the node's `settings.json`.
- Use `flow.py` to declare the inputs, node connections, and output. Keep `nodes` in the sheet's `tool.workspace.sources` list.
- Run `workspace check` and verify the changed flow with `workspace run` from the sheet directory.
"""


def discover_flows(root: Path) -> list[tuple[str, Path]]:
    folder = root / "flows"
    flows = []
    if folder.is_dir():
        flows = [(path.name, path) for path in sorted(folder.iterdir()) if (path / "flow.py").is_file()]
    if (root / "flow.py").is_file():
        flows.insert(0, ("Main", root))
    return flows


def sheet_source_files(root: Path, *, main: bool) -> Iterator[Path]:
    for directory, folders, files in os.walk(root):
        path = Path(directory)
        folders[:] = [
            name for name in folders
            if not name.startswith(".") and name != "__pycache__"
            and not (main and path == root and name == "flows")
            and not (path / name / "pyvenv.cfg").is_file()
        ]
        for name in files:
            if name.endswith(".py") or (path == root and name == "pyproject.toml"):
                yield path / name


def source_stamp(root: Path, *, main: bool = False) -> str:
    """Compute a working-tree stamp across the sheet's Python files and pyproject.toml."""
    entries = []
    for source in sheet_source_files(root, main=main):
        try:
            stat = source.stat(follow_symlinks=False)
        except OSError:
            continue
        entries.append((str(source.relative_to(root)), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    return sha256(json.dumps(sorted(entries)).encode()).hexdigest()


def can_delete_sheet(workspace_root: Path, sheet_root: Path) -> bool:
    """Return True when sheet_root is a direct, non-symlink folder under workspace_root/flows."""
    return (
        sheet_root.parent == workspace_root / "flows"
        and not sheet_root.parent.is_symlink()
        and not sheet_root.is_symlink()
    )


def archive_sheet_directory(
    workspace_root: Path, sheet_root: Path, move_fn: Callable[[Path], None],
) -> None:
    """Archive a deleted sheet folder under .workspace/deleted-flows without following symlinks."""
    trash = workspace_root / ".workspace" / "deleted-flows"
    if trash.parent.is_symlink() or trash.is_symlink():
        raise OSError
    archive = trash / uuid4().hex
    archive.mkdir(parents=True)
    try:
        move_fn(archive / sheet_root.name)
    except OSError:
        archive.rmdir()
        raise


def edit_sheet_flow_source(
    sheet_root: Path, source: str | None, revision: str | None,
) -> dict[str, str]:
    """Read or update a sheet's flow.py entrypoint with symlink and revision checks."""
    path = sheet_root / "flow.py"
    if path.resolve() != path or not path.is_file():
        raise FileNotFoundError
    return edit_source_file(path, source, revision)


def write_new_file(path: Path, content: str, cleanup: ExitStack) -> None:
    with path.open("x", encoding="utf-8") as stream:
        cleanup.callback(path.unlink, missing_ok=True)
        stream.write(content)


def write_starter_flow(directory: Path, cleanup: ExitStack) -> Path:
    nodes = directory / "nodes"
    node = nodes / "start"
    for folder in (nodes, node):
        folder.mkdir()
        cleanup.callback(folder.rmdir)
    for path, content in (
        (nodes / "__init__.py", ""),
        (node / "__init__.py", ""),
        (node / "entrypoint.py", STARTER_NODE),
        (node / "settings.json", "{}\n"),
    ):
        write_new_file(path, content, cleanup)
    pending = directory / ".flow.py.tmp"
    entrypoint = directory / "flow.py"
    write_new_file(pending, STARTER_FLOW, cleanup)
    pending.rename(entrypoint)
    cleanup.callback(entrypoint.unlink, missing_ok=True)
    return entrypoint


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

    with ExitStack() as cleanup:
        cleanup.callback(directory.rmdir)
        write_new_file(directory / "pyproject.toml", STARTER_SOURCES, cleanup)
        entrypoint = write_starter_flow(directory, cleanup)
        cleanup.pop_all()
    return entrypoint


def initialize_workspace(root: Path, *, with_main: bool = False) -> None:
    settings = load_ui_settings(root, None)
    project = (
        '[project]\n'
        'name = "workspace-project"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.12"\n'
        f'dependencies = ["workspace-sdk=={version("workspace-sdk")}"]\n'
    )
    root.mkdir(parents=True, exist_ok=True)
    allowed = {".DS_Store", ".venv"} if with_main else {".DS_Store"}
    if any(path.name not in allowed for path in root.iterdir()):
        raise ValueError(f"Choose an empty folder to initialize a Workspace: {root}")
    files = {
        "setting.json": json.dumps({"port": settings.port, "zoom": settings.zoom}, indent=2) + "\n",
        "pyproject.toml": project + ("\n" + STARTER_SOURCES if with_main else ""),
        "AGENTS.md": WORKSPACE_AGENT_GUIDE,
    }
    with ExitStack() as cleanup:
        for name, content in files.items():
            write_new_file(root / name, content, cleanup)
        if with_main:
            write_starter_flow(root, cleanup)
        cleanup.pop_all()
