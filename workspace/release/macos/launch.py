"""Prepare the selected project's environment and launch its Workspace UI."""

from pathlib import Path
import signal
import subprocess
import sys
from tempfile import NamedTemporaryFile
from time import monotonic, sleep
import tomllib


def start_ui(project: Path, cli: Path) -> tuple[subprocess.Popen, str]:
    state = project / ".workspace"
    state.mkdir(exist_ok=True)
    command = [str(cli), "--project", str(project), "ui"]
    with NamedTemporaryFile(mode="w", encoding="utf-8", prefix="launcher-", suffix=".log", dir=state, delete=False) as log:
        logfile = Path(log.name)
        process = subprocess.Popen(
            command, cwd=project, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
    try:
        deadline = monotonic() + 10
        with logfile.open(encoding="utf-8") as log:
            while monotonic() < deadline:
                for line in log:
                    if line.startswith("Workspace UI: "):
                        return process, line.removeprefix("Workspace UI: ").strip()
                if process.poll() is not None:
                    break
                sleep(0.05)
        error = logfile.read_text().strip()
        if "Address already in use" in error:
            raise ValueError("The configured UI port is already in use. Choose a free port in setting.json.")
        raise ValueError(error or f"Workspace did not start. See {logfile}.")
    except BaseException:
        stop_ui(process)
        raise


def stop_ui(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main(project: Path) -> None:
    resources = Path(__file__).resolve().parent
    wheel, = resources.glob("workspace_sdk-*.whl")
    uv = "uv"
    if not project.is_dir():
        raise ValueError(f"Choose a Workspace folder: {project}")

    if not any((project / name).exists() for name in ("setting.json", "pyproject.toml", "flow.py", "flows")):
        sys.path.insert(0, str(wheel))
        from workspace_sdk.sheets import initialize_workspace

        initialize_workspace(project)

    interpreter = project / ".venv" / "bin" / "python"
    if not interpreter.is_file():
        if (project / ".venv").exists():
            raise ValueError(f"The existing environment has no usable Python: {interpreter}")
        subprocess.run([uv, "venv", "--python", sys.executable, ".venv"], cwd=project, check=True)

    install = [uv, "pip", "install", "--python", str(interpreter), str(wheel)]
    metadata = project / "pyproject.toml"
    if metadata.is_file() and tomllib.loads(metadata.read_text()).get("project", {}).get("dependencies"):
        install.extend(["-r", str(metadata)])
    subprocess.run(install, cwd=project, check=True)

    process, url = start_ui(project, interpreter.with_name("workspace"))
    try:
        subprocess.run(["/usr/bin/open", url], check=True)
    except (OSError, subprocess.CalledProcessError):
        stop_ui(process)
        raise


if __name__ == "__main__":
    try:
        main(Path(sys.argv[1]).expanduser().resolve())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"Workspace setup: {error}")
