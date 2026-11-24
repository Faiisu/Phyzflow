"""Discover and create the Python projects represented by Workspace sheets."""

import ast
from collections.abc import Iterator
from contextlib import ExitStack
from graphlib import CycleError, TopologicalSorter
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable
from unicodedata import category, normalize
from uuid import uuid4

from .node_sources import edit_source_file, read_node_source
from .project import WORKSPACE_FORMAT_VERSION, workspace_format_notice
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

STARTER_SOURCES = 'sources = ["nodes"]\n'

WORKSPACE_AGENT_GUIDE = """# Workspace instructions

- Use one Python package per node under the sheet's `nodes/` directory.
- Put the node callable in `nodes/<node_name>/entrypoint.py` and keep its helpers in that package.
- Put shared default values for settings declared in `Step.settings` in the node's `settings.json`.
- Use `flow.py` to declare the inputs, node connections, and output. Keep `nodes` in the sheet's `tool.workspace.sources` list.
- Run `workspace check` and verify the changed flow with `workspace run` from the sheet directory.
"""


def discover_flows(root: Path) -> list[tuple[str, Path]]:
    workspace_format_notice(root)
    folder = root / "flows"
    flows = []
    if folder.is_dir() and folder.resolve() == folder.absolute():
        flows = [(sheet_name(path, path.name), path) for path in sorted(folder.iterdir()) if _is_sheet_directory(path)]
    if _is_sheet_directory(root):
        flows.insert(0, (sheet_name(root, "Main"), root))
    return flows


def _is_sheet_directory(path: Path) -> bool:
    if not path.is_dir() or path.resolve() != path.absolute():
        return False
    entrypoint = path / "flow.py"
    return not entrypoint.is_symlink() and entrypoint.is_file()


def find_sheet_by_path(root: Path, relative: str | Path) -> Path | None:
    if not relative or "\x00" in str(relative):
        return None
    relative = Path(relative)
    if relative.anchor or ".." in relative.parts:
        return None
    path = root / relative
    if (path == root or path.parent == root / "flows") and _is_sheet_directory(path):
        return path
    return None


def find_sheet(root: Path, name: str) -> Path | None:
    return next((path for title, path in discover_flows(root)
                 if title == name or (name == "Main" and path == root)), None)


def sheet_name(path: Path, default: str) -> str:
    file = path / ".workspace" / "sheet-name"
    if path.is_symlink() or file.parent.is_symlink() or file.is_symlink() or not file.is_file():
        return default
    try:
        with file.open("rb") as stream:
            name = validate_sheet_name(stream.read(258).decode())
        return name if default == "Main" or name.casefold() != "main" else default
    except (OSError, ValueError):
        return default


def set_sheet_name(path: Path, name: str) -> None:
    directory = path / ".workspace"
    if directory.is_symlink():
        raise ValueError("Workspace state must not be a symbolic link.")
    directory.mkdir(mode=0o700, exist_ok=True)
    pending = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
            pending = Path(stream.name)
            stream.write(name + "\n")
        pending.replace(directory / "sheet-name")
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def workspace_root_for_sheet(root: Path) -> Path:
    parent = root.parent.parent
    if root.parent.name == "flows" and (root / "flow.py").is_file() and (
        (parent / "pyproject.toml").is_file() or (parent / "flow.py").is_file() or (parent / ".venv").is_dir()
    ):
        return parent
    return root


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


def editable_sheet_python_files(root: Path, *, main: bool) -> Iterator[Path]:
    for path in sheet_source_files(root, main=main):
        if path.suffix == ".py" and not path.name.startswith(".") and not path.is_symlink() and path.is_file():
            yield path


def declared_graph(root: Path, files: set[str], *, require_export: bool = False) -> list[dict]:
    if "flow.py" not in files:
        return []
    try:
        module = ast.parse(read_node_source(root / "flow.py"))
    except (OSError, ValueError, SyntaxError, RecursionError):
        return []
    imports = {}
    for statement in module.body:
        if isinstance(statement, ast.ImportFrom) and statement.module and not statement.level:
            path = statement.module.replace(".", "/")
            for alias in statement.names:
                imports[alias.asname or alias.name] = next(
                    (file for file in (f"{path}.py", f"{path}/__init__.py") if file in files), "flow.py",
                )
    if require_export:
        flow_names = [node for node in ast.walk(module) if isinstance(node, ast.Name) and node.id == "flow"]
        flow = next((node.value for node in module.body if isinstance(node, ast.Assign)
                     and node.targets == flow_names), None)
    else:
        flow = next((node for node in ast.walk(module) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == "Flow"), None)
    if not isinstance(flow, ast.Call) or not isinstance(flow.func, ast.Name) or flow.func.id != "Flow":
        return []
    steps = next((item.value for item in flow.keywords if item.arg == "steps"),
                 flow.args[1] if len(flow.args) > 1 else None)
    if not isinstance(steps, (ast.Tuple, ast.List)):
        return []

    def referenced_steps(call_name: str) -> list[str]:
        if arguments is None:
            return []
        refs = []
        for ref in ast.walk(arguments):
            if not (isinstance(ref, ast.Call) and isinstance(ref.func, ast.Name) and ref.func.id == call_name):
                continue
            target = ref.args[0] if ref.args else next(
                (kw.value for kw in ref.keywords if kw.arg == "step_id"),
                None,
            )
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                refs.append(target.value)
        return list(dict.fromkeys(refs))

    graph = {}
    for step in steps.elts:
        if not isinstance(step, ast.Call) or not isinstance(step.func, ast.Name) or step.func.id != "Step":
            return []
        args = {name: value for name, value in zip(("id", "function", "arguments"), step.args)}
        args.update((item.arg, item.value) for item in step.keywords)
        name, function, arguments = (args.get(key) for key in ("id", "function", "arguments"))
        if not isinstance(name, ast.Constant) or not isinstance(name.value, str) or name.value in graph:
            return []
        path = imports.get(function.id, "flow.py") if isinstance(function, ast.Name) else "flow.py"
        dependencies = referenced_steps("Output")
        loop_dependencies = referenced_steps("LoopOutput")
        graph[name.value] = {
            "id": name.value, "source": path, "file": path, "description": "",
            "dependencies": dependencies, "loop_dependencies": loop_dependencies, "arguments": [],
        }
    if any(
        dependency not in graph
        for step in graph.values()
        for dependency in (*step["dependencies"], *step["loop_dependencies"])
    ):
        return []
    try:
        order = tuple(TopologicalSorter({key: step["dependencies"] for key, step in graph.items()}).static_order())
    except CycleError:
        return []
    return [graph[key] for key in order]


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
    sheet_root: Path,
    source: str | None,
    revision: str | None,
    relative: str = "flow.py",
    *,
    main: bool = False,
) -> dict[str, str]:
    if relative == "flow.py":
        path = sheet_root / "flow.py"
        if path.resolve() != path or not path.is_file():
            raise FileNotFoundError
        return edit_source_file(path, source, revision)
    for file in editable_sheet_python_files(sheet_root, main=main):
        if file.relative_to(sheet_root).as_posix() == relative and file.resolve() == file:
            return edit_source_file(file, source, revision)
    raise FileNotFoundError


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


def validate_sheet_name(value: object) -> str:
    name = normalize("NFC", value.strip()) if isinstance(value, str) else ""
    if not (
        0 < len(name) <= 64 and name[0].isalnum()
        and all(char.isalnum() or char in " _-" or category(char).startswith("M") for char in name)
    ):
        raise ValueError("Start with a letter or number and use up to 64 letters, numbers, spaces, underscores, or hyphens.")
    return name


def create_sheet(root: Path, value: object) -> Path:
    name = validate_sheet_name(value)
    parent = root / "flows"
    if parent.is_symlink():
        raise ValueError("The flows directory must be inside this Workspace.")
    parent.mkdir(exist_ok=True)
    if name.casefold() == "main" or any(title.casefold() == name.casefold() for title, _ in discover_flows(root)) or any(
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
        write_new_file(directory / "pyproject.toml", "[tool.workspace]\n" + STARTER_SOURCES, cleanup)
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
        '\n[tool.workspace]\n'
        f'format_version = {WORKSPACE_FORMAT_VERSION}\n'
    )
    root.mkdir(parents=True, exist_ok=True)
    allowed = {".DS_Store", ".venv"} if with_main else {".DS_Store"}
    if any(path.name not in allowed for path in root.iterdir()):
        raise ValueError(f"Choose an empty folder to initialize a Workspace: {root}")
    files = {
        "setting.json": json.dumps({"port": settings.port, "zoom": settings.zoom}, indent=2) + "\n",
        "pyproject.toml": project + (STARTER_SOURCES if with_main else ""),
        "AGENTS.md": WORKSPACE_AGENT_GUIDE,
    }
    with ExitStack() as cleanup:
        for name, content in files.items():
            write_new_file(root / name, content, cleanup)
        if with_main:
            write_starter_flow(root, cleanup)
        cleanup.pop_all()
