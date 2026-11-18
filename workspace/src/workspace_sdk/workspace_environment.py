"""Provision and manage the shared Python interpreter for a registered Workspace."""

import os
from pathlib import Path
from shutil import rmtree
import stat
import subprocess


PACKAGE_ARCHIVE_SUFFIXES = (".whl", ".zip", ".tar.gz", ".tgz")
MAX_INSTALL_PACKAGES = 32


def workspace_interpreter(root: Path) -> Path:
    return root / ".venv" / "bin" / "python"


def same_folder(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return left.samefile(right)
    except (FileNotFoundError, NotADirectoryError):
        return False


def folders_overlap(left: Path, right: Path) -> bool:
    return (
        any(same_folder(left, parent) for parent in (right, *right.parents))
        or any(same_folder(right, parent) for parent in left.parents)
    )


def _verify_python_312_venv(environment: Path, interpreter: Path) -> None:
    try:
        if environment.is_symlink():
            raise ValueError
        if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
            raise ValueError
        result = subprocess.run(
            [interpreter, "-I", "-c", "import sys; print(sys.prefix); print(*sys.version_info[:2], sep='.')"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        prefix, version = result.stdout.strip().splitlines()
        if Path(prefix).resolve() != environment.resolve() or not (environment / "pyvenv.cfg").is_file():
            raise ValueError
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ValueError(f"The existing environment at {environment} is broken. Repair or move it and try again.") from None
    if version != "3.12":
        raise ValueError(f"The environment at {environment} uses Python {version}. Repair or move it and try again with Python 3.12.")


def ensure_workspace_environment(root: Path) -> Path:
    environment = root / ".venv"
    interpreter = workspace_interpreter(root)
    if environment.exists() or environment.is_symlink():
        _verify_python_312_venv(environment, interpreter)
        return interpreter

    try:
        result = subprocess.run(
            ["uv", "venv", "--no-project", "--python", "3.12", environment],
            cwd=root, capture_output=True, text=True, timeout=180,
        )
        if result.returncode or not interpreter.is_file():
            raise RuntimeError(f"uv could not create the Python 3.12 environment. {result.stderr.strip()}")
    except FileNotFoundError:
        raise RuntimeError("Install uv and make it available on PATH to create the Workspace environment.") from None
    except (OSError, subprocess.SubprocessError, RuntimeError):
        if environment.is_dir() and not environment.is_symlink():
            rmtree(environment)
        raise
    return interpreter


def _is_archive_name(name: str) -> bool:
    return name.lower().endswith(PACKAGE_ARCHIVE_SUFFIXES)


def _is_path_spec(item: str, root: Path) -> bool:
    if "/" in item or "\\" in item or _is_archive_name(item):
        return True
    candidate = root / item
    return candidate.exists() or candidate.is_symlink()


def _resolve_package_spec(
    item: object,
    root: Path,
    protected_folders: tuple[Path, ...],
) -> tuple[str, bool]:
    if not isinstance(item, str):
        raise ValueError("Each package specification must be a string.")
    spec = item.strip()
    if not spec or any(char in spec for char in "\x00\r\n"):
        raise ValueError("Package specifications must be non-empty single-line strings.")
    if spec.startswith("-"):
        raise ValueError("Package options and flags starting with '-' are not allowed.")
    lower = spec.lower()
    if "file:" in lower:
        raise ValueError("File URLs are not supported; pass a local .whl or archive path.")
    if "@" in spec:
        _, target = spec.split("@", 1)
        if not target.strip().lower().startswith("https://"):
            raise ValueError("Direct package URLs must use https://.")
    if not _is_path_spec(spec, root):
        return spec, False

    raw_path = Path(spec) if Path(spec).is_absolute() else (root / spec)
    if raw_path.is_symlink():
        raise ValueError("Package symbolic links are not allowed.")
    resolved = raw_path.resolve(strict=False)
    for folder in protected_folders:
        if folders_overlap(raw_path, folder) or folders_overlap(resolved, folder):
            raise ValueError("Protected application paths cannot be used as package sources.")
    for internal in (root / ".workspace", root / ".venv"):
        if folders_overlap(raw_path, internal) or folders_overlap(resolved, internal):
            raise ValueError("Workspace internal directories cannot be used as package sources.")
    if not resolved.is_file() or not _is_archive_name(resolved.name):
        raise ValueError("Local package paths must refer to an existing .whl, .zip, or .tar.gz archive.")
    try:
        info = resolved.stat(follow_symlinks=False)
    except OSError as error:
        raise ValueError(f"Could not access package archive: {error.strerror or error}.") from None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Local package paths must refer to a regular package archive.")
    return str(resolved), True


def install_workspace_packages(
    root: Path,
    packages: list[str],
    *,
    protected_folders: tuple[Path, ...] = (),
) -> dict:
    environment = root / ".venv"
    interpreter = workspace_interpreter(root)
    if not environment.exists() or not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise ValueError("Workspace Python environment is unavailable.")
    _verify_python_312_venv(environment, interpreter)

    if not isinstance(packages, list) or not 1 <= len(packages) <= MAX_INSTALL_PACKAGES:
        raise ValueError(f"Provide 1 to {MAX_INSTALL_PACKAGES} packages to install.")

    resolved_specs: list[str] = []
    local_flags: list[bool] = []
    for item in packages:
        resolved_spec, is_local = _resolve_package_spec(item, root, protected_folders)
        resolved_specs.append(resolved_spec)
        local_flags.append(is_local)

    all_local_wheels = all(local_flags)
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("PHYZFLOW_") and key not in ("PYTHONPATH", "PYTHONHOME")
    }
    env["VIRTUAL_ENV"] = str(environment)
    env["PYTHONNOUSERSITE"] = "1"
    command = [
        "uv", "pip", "install", "--python", str(interpreter),
        *(("--no-index",) if all_local_wheels else ()),
        "--", *resolved_specs,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError:
        raise RuntimeError("Install uv and make it available on PATH to install Workspace packages.") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("Package installation timed out.") from None
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail[:4096] or "Could not install Workspace packages.")
    return {
        "installed": list(packages),
        "interpreter": str(interpreter),
        "output": (result.stdout or "")[:4096],
    }
