"""Inspect and edit existing source files within a node's folder."""

from codecs import BOM_UTF8
from hashlib import sha256
from io import BytesIO
from os import link, scandir
from pathlib import Path
from stat import S_IMODE
from tempfile import NamedTemporaryFile
from tokenize import detect_encoding
from typing import TypedDict


MAX_SOURCE_BYTES = 128 * 1024
SOURCE_EXTENSIONS = {".py", ".json"}
SETTINGS_FILENAME = "settings.json"


class SourceChanged(RuntimeError):
    pass


class NodeFile(TypedDict):
    name: str
    children: list["NodeFile"] | None


def node_location(root: Path, filename: str | None) -> tuple[Path, Path] | None:
    source = (root / filename).resolve() if filename else None
    if source is None or not source.is_relative_to(root) or not source.is_file():
        return None
    relative = source.relative_to(root)
    folder = root.joinpath(*relative.parts[:2]) if relative.parts[0] == "nodes" and len(relative.parts) >= 3 else source.parent
    return folder, source


def node_settings_path(root: Path, filename: str | None) -> Path | None:
    location = node_location(root, filename)
    if location is not None:
        folder, _ = location
        parts = folder.relative_to(root).parts
        if len(parts) == 2 and parts[0] == "nodes":
            return folder / SETTINGS_FILENAME
    return None


def node_files(root: Path, filename: str | None, *, include_missing_settings: bool = True) -> dict:
    location = node_location(root, filename)
    if location is None:
        return {"path": None, "entries": [], "truncated": False}
    folder, source = location
    if folder == root:
        return {"path": ".", "entries": [{"name": source.name, "children": None}], "truncated": False}

    remaining = 500
    truncated = False

    def read_folder(directory: Path, depth: int) -> list[NodeFile]:
        nonlocal remaining, truncated
        entries: list[NodeFile] = []
        if depth >= 20:
            truncated = True
            return entries
        with scandir(directory) as children:
            for child in children:
                if child.name.startswith(".") or child.name == "__pycache__" or child.is_symlink():
                    continue
                if remaining == 0:
                    truncated = True
                    break
                remaining -= 1
                entries.append({
                    "name": child.name,
                    "children": read_folder(Path(child.path), depth + 1) if child.is_dir(follow_symlinks=False) else None,
                })
        return sorted(entries, key=lambda item: (item["children"] is None, item["name"].casefold(), item["name"]))

    entries = read_folder(folder, 0)
    settings = node_settings_path(root, filename)
    settings_path = None
    if settings is not None and not settings.is_symlink() and (
        settings.is_file() or (include_missing_settings and not settings.exists())
    ):
        entries = [{"name": SETTINGS_FILENAME, "children": None}] + [
            entry for entry in entries if entry["name"] != SETTINGS_FILENAME
        ]
        settings_path = settings.relative_to(root).as_posix()
    return {"path": folder.relative_to(root).as_posix(), "entries": entries, "truncated": truncated,
            "settings": settings_path}


def node_source_path(root: Path, filename: str | None, relative: str) -> Path:
    location = node_location(root, filename)
    path = Path(relative)
    if (
        location is None or path.is_absolute() or path.suffix not in SOURCE_EXTENSIONS
        or any(part.startswith(".") or part == "__pycache__" for part in path.parts)
    ):
        raise FileNotFoundError
    folder, source = location
    path = root / path
    if (
        not path.is_relative_to(folder) or (folder == root and path != source)
        or path.resolve() != path
        or not (path.is_file() or (path == node_settings_path(root, filename) and not path.exists()))
    ):
        raise FileNotFoundError
    return path


def read_node_source(path: Path) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(MAX_SOURCE_BYTES + 1)
    if len(content) > MAX_SOURCE_BYTES:
        raise ValueError("The editor supports source files up to 128 KiB.")
    return content


def source_revision(content: bytes) -> str:
    return sha256(content).hexdigest()


def edit_source_file(
    path: Path, source: str | None, revision: str | None, *, initial: str | None = None,
) -> dict[str, str]:
    try:
        content = read_node_source(path)
    except FileNotFoundError:
        if initial is None or path.resolve() != path:
            raise
        content = initial.encode("utf-8")
        current_revision = "missing"
    else:
        current_revision = source_revision(content)
    if source is not None and revision != current_revision:
        raise SourceChanged

    python_source = path.suffix == ".py"
    if python_source:
        encoding, _ = detect_encoding(BytesIO(content).readline)
    else:
        encoding = "utf-8-sig" if content.startswith(BOM_UTF8) else "utf-8"
    try:
        text = content.decode(encoding)
    except LookupError:
        raise ValueError("Could not read the file. Check the Python encoding declaration.") from None
    if source is None:
        return {"source": text.replace("\r\n", "\n").replace("\r", "\n"), "revision": current_revision}

    if "\r\n" in text and "\n" not in text.replace("\r\n", ""):
        source = source.replace("\r\n", "\n").replace("\n", "\r\n")
    updated = source.encode(encoding)
    if len(updated) > MAX_SOURCE_BYTES:
        raise ValueError("The editor supports source files up to 128 KiB.")
    if updated == content and current_revision != "missing":
        return {"revision": source_revision(content)}
    if python_source:
        message = "Saving would change how the file is read. Check the Python encoding declaration."
        try:
            saved_encoding, _ = detect_encoding(BytesIO(updated).readline)
            reopened = updated.decode(saved_encoding)
        except (LookupError, SyntaxError, UnicodeError):
            raise ValueError(message) from None
        if reopened != source:
            raise ValueError(message)

    with NamedTemporaryFile(dir=path.parent, prefix=".workspace-edit-", delete=False) as stream:
        pending = Path(stream.name)
        try:
            stream.write(updated)
            stream.close()
            if current_revision == "missing":
                if path.resolve() != path:
                    raise SourceChanged
                try:
                    link(pending, path)
                except FileExistsError:
                    raise SourceChanged from None
            else:
                pending.chmod(S_IMODE(path.stat().st_mode))
                if path.resolve() != path or read_node_source(path) != content:
                    raise SourceChanged
                pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)
    return {"revision": source_revision(updated)}
