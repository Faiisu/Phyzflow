"""Inspect and edit existing source files within a node's folder."""

from hashlib import sha256
from io import BytesIO
from os import scandir
from pathlib import Path
from stat import S_IMODE
from tempfile import NamedTemporaryFile
from tokenize import detect_encoding
from typing import TypedDict


MAX_SOURCE_BYTES = 128 * 1024


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


def node_files(root: Path, filename: str | None) -> dict:
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
    return {"path": folder.relative_to(root).as_posix(), "entries": entries, "truncated": truncated}


def node_source_path(root: Path, filename: str | None, relative: str) -> Path:
    location = node_location(root, filename)
    path = Path(relative)
    if (
        location is None or path.is_absolute() or path.suffix != ".py"
        or any(part.startswith(".") or part == "__pycache__" for part in path.parts)
    ):
        raise FileNotFoundError
    folder, source = location
    path = root / path
    if (
        not path.is_relative_to(folder) or (folder == root and path != source)
        or path.resolve() != path or not path.is_file()
    ):
        raise FileNotFoundError
    return path


def read_node_source(path: Path) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(MAX_SOURCE_BYTES + 1)
    if len(content) > MAX_SOURCE_BYTES:
        raise ValueError("The editor supports Python files up to 128 KiB.")
    return content


def source_revision(content: bytes) -> str:
    return sha256(content).hexdigest()


def edit_node_source(path: Path, source: str | None, revision: str | None) -> dict[str, str]:
    content = read_node_source(path)
    encoding, _ = detect_encoding(BytesIO(content).readline)
    text = content.decode(encoding)
    if source is None:
        return {"source": text.replace("\r\n", "\n").replace("\r", "\n"), "revision": source_revision(content)}

    if revision != source_revision(content):
        raise SourceChanged
    if "\r\n" in text and "\n" not in text.replace("\r\n", ""):
        source = source.replace("\r\n", "\n").replace("\n", "\r\n")
    updated = source.encode(encoding)
    if len(updated) > MAX_SOURCE_BYTES:
        raise ValueError("The editor supports Python files up to 128 KiB.")
    if updated == content:
        return {"revision": source_revision(content)}
    with NamedTemporaryFile(dir=path.parent, prefix=".workspace-edit-", delete=False) as stream:
        pending = Path(stream.name)
        try:
            stream.write(updated)
            stream.close()
            pending.chmod(S_IMODE(path.stat().st_mode))
            if path.resolve() != path or read_node_source(path) != content:
                raise SourceChanged
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)
    return {"revision": source_revision(updated)}
