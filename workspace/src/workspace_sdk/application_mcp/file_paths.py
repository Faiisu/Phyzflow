import os
import stat
from contextlib import nullcontext
from functools import wraps
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from typing import Literal

from ..sheet_locks import SheetEditLease
from .access import ensure_not_protected

STAGING_PREFIX = ".phyzflow-transfer-"
RESERVED_PARTS = {".git", ".workspace", ".venv", "__pycache__"}
FileMode = Literal["missing", "file", "directory"]
LINUX_AT_FDCWD = -100
LINUX_RENAME_NOREPLACE = 1
MACOS_RENAME_EXCL = 4


def file_errors(operation):
    @wraps(operation)
    def run(*args, **kwargs):
        from mcp.server.mcpserver.exceptions import ToolError

        try:
            return operation(*args, **kwargs)
        except (ValueError, OSError, RuntimeError) as error:
            if isinstance(error, OSError) and error.filename:
                raise ToolError(error.strerror or "The filesystem operation failed.") from None
            raise ToolError(str(error)) from None

    return run


def reserved(name: str) -> bool:
    lower = name.casefold()
    return lower in RESERVED_PARTS or lower.startswith(STAGING_PREFIX)


def relative_path(value: str, *, directory: bool = False) -> Path:
    if directory and value == ".":
        return Path(".")
    parts = value.split("/")
    if any(
        not part
        or part in (".", "..")
        or "\\" in part
        or "\x00" in part
        or reserved(part)
        or (os.name == "nt" and (
            part[-1] in " ." or PureWindowsPath(part).is_reserved()
            or any(char in ':<>|?*"' or ord(char) < 32 for char in part)
        ))
        for part in parts
    ):
        raise ValueError("Expected an accessible Workspace-relative path.")
    return Path(*parts)


def path_info(path: Path) -> tuple[FileMode, os.stat_result | None]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "missing", None
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise ValueError("Symbolic links and reparse points are outside the file scope.")
    if stat.S_ISDIR(info.st_mode):
        return "directory", info
    if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
        return "file", info
    raise ValueError("Only regular files and directories without file aliases are accessible.")


def checked_path(root: Path, relative: Path, protected: tuple[Path, ...]) -> tuple[Path, FileMode, os.stat_result | None]:
    if root.resolve() != root or path_info(root)[0] != "directory":
        raise ValueError("The Workspace folder is unavailable.")
    target = root
    mode, info = "directory", None
    for index, part in enumerate(relative.parts):
        target /= part
        mode, info = path_info(target)
        if index < len(relative.parts) - 1 and mode == "file":
            raise ValueError("A parent path is a file.")
        if mode == "directory" and os.path.lexists(target / "pyvenv.cfg"):
            raise ValueError("Python environment paths are outside the file scope.")
    ensure_not_protected(target, protected)
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Path must stay inside the Workspace.")
    return target, mode, info


def revision(info: os.stat_result) -> str:
    fields = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return sha256(repr(fields).encode("ascii")).hexdigest()


def file_metadata(relative: Path, info: os.stat_result) -> dict:
    return {"path": relative.as_posix(), "size": info.st_size, "revision": revision(info)}


def require_file(mode: FileMode, info: os.stat_result | None) -> os.stat_result:
    if mode != "file" or info is None:
        raise ValueError("The path must point to a regular file.")
    return info


def verify_revision(mode: FileMode, info: os.stat_result | None, expected: str) -> None:
    if mode == "missing" and expected == "missing":
        return
    if mode != "file" or info is None or revision(info) != expected:
        raise ValueError("The file revision changed. Read the latest revision and retry.")


def open_file(target: Path, info: os.stat_result):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    stream = os.fdopen(os.open(target, flags), "rb")
    opened = os.fstat(stream.fileno())
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or revision(opened) != revision(info):
        stream.close()
        raise ValueError("The file changed while opening it.")
    return stream


def is_entrypoint(relative: Path) -> bool:
    parts = tuple(part.casefold() for part in relative.parts)
    return parts == ("flow.py",) or (len(parts) == 3 and parts[0] == "flows" and parts[-1] == "flow.py")



def owning_sheet(root: Path, relative: Path) -> Path | None:
    if len(relative.parts) < 2 or relative.parts[0].casefold() != "flows":
        candidate = root
    else:
        alias = root / relative.parts[0]
        conventional = root / "flows"
        if alias != conventional and (not conventional.exists() or not alias.exists() or not alias.samefile(conventional)):
            candidate = root
        elif len(relative.parts) < 3:
            return None
        else:
            candidate = root / Path(*relative.parts[:2])
    try:
        mode, _ = path_info(candidate / "flow.py")
    except ValueError:
        return None
    return candidate if mode == "file" else None


def mutate_files(access, workspace_id: str, paths: tuple[Path, ...], session_ids: dict[str, str], operation):
    with access.file_gate:
        root = access.workspace(workspace_id)
        resolver = access.mounted_sheet_resolver(workspace_id)
        with resolver.file_guard if resolver is not None else nullcontext():
            mounted_leases = resolver.file_leases() if resolver is not None else {}
            leases = {}
            for relative in paths:
                target, _, _ = checked_path(root, relative, access.protected_folders)
                sheet_root = owning_sheet(root, relative)
                if sheet_root is not None:
                    lease = mounted_leases.get(sheet_root)
                    if lease is None:
                        lease = next((item for path, item in mounted_leases.items()
                                      if path.is_dir() and path.samefile(sheet_root)), None)
                    physical_root = lease.sheet_root if lease is not None else sheet_root
                    key = physical_root.relative_to(root).as_posix()
                    if key not in leases:
                        leases[key] = (lease or SheetEditLease(physical_root), [])
                    _, affected = leases[key]
                    affected.append(Path(*target.relative_to(root).parts[len(Path(key).parts):]).as_posix())
            if session_ids.keys() - leases.keys():
                raise ValueError("A session ID must identify an affected Sheet by its Workspace-relative directory.")
            principal = access.principal()
            keys = sorted(leases)

            def apply_at(index):
                if index == len(keys):
                    return operation(root)
                key = keys[index]
                lease, affected = leases[key]
                return lease.write_locked(
                    principal, session_ids.get(key), lambda: apply_at(index + 1), paths=tuple(affected),
                )

            return apply_at(0)


def atomic_move(source: Path, destination: Path) -> None:
    if source.parent.stat().st_dev != destination.parent.stat().st_dev:
        raise ValueError("Moves across filesystems are unavailable.")
    if os.name == "nt":
        source.rename(destination)
        return
    import ctypes
    import sys

    library = ctypes.CDLL(None, use_errno=True)
    source_bytes, destination_bytes = os.fsencode(source), os.fsencode(destination)
    if sys.platform == "darwin":
        result = library.renamex_np(source_bytes, destination_bytes, MACOS_RENAME_EXCL)
    else:
        rename = getattr(library, "renameat2", None)
        if rename is None:
            raise ValueError("This host does not support no-overwrite file moves.")
        result = rename(LINUX_AT_FDCWD, source_bytes, LINUX_AT_FDCWD, destination_bytes, LINUX_RENAME_NOREPLACE)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
