from pathlib import Path

from .file_paths import path_info, relative_path

UPLOAD_DIRECTORY = Path(".workspace/files")


def file_folder(root: Path, path: str, *, create: bool = False) -> Path | None:
    relative = relative_path(path, directory=True)
    if root.resolve() != root or path_info(root)[0] != "directory":
        raise FileNotFoundError("This folder is unavailable.")
    folder = root
    for part in (*UPLOAD_DIRECTORY.parts, *relative.parts):
        folder /= part
        if create and not relative.parts:
            folder.mkdir(mode=0o700, exist_ok=True)
        mode, _ = path_info(folder)
        if mode == "missing" and not relative.parts and not create:
            return None
        if mode != "directory":
            raise FileNotFoundError("This folder is unavailable.")
    return folder


def file_name(name: object) -> str:
    if not isinstance(name, str) or not name.strip() or "/" in name or any(ord(char) < 32 for char in name):
        raise ValueError("Enter a valid file or folder name.")
    relative_path(name)
    name.encode("utf-8")
    return name


def create_upload_folder(root: Path, path: str, name: str) -> None:
    folder = file_folder(root, path, create=True)
    assert folder is not None
    (folder / name).mkdir(mode=0o700)


def folder_entries(folder: Path | None) -> list[dict]:
    if folder is None:
        return []
    entries = []
    for child in folder.iterdir():
        try:
            file_name(child.name)
            mode, info = path_info(child)
        except ValueError:
            continue
        if mode != "missing":
            entries.append({"name": child.name, "directory": mode == "directory", "size": info.st_size})
    return sorted(entries, key=lambda entry: (not entry["directory"], entry["name"].casefold()))
