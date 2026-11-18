"""Provision the shared Python interpreter for a registered Workspace."""

from pathlib import Path
from shutil import rmtree
import subprocess


def workspace_interpreter(root: Path) -> Path:
    return root / ".venv" / "bin" / "python"


def ensure_workspace_environment(root: Path) -> Path:
    environment = root / ".venv"
    interpreter = workspace_interpreter(root)
    if environment.exists() or environment.is_symlink():
        try:
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
