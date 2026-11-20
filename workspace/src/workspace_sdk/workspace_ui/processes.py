from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import re
from subprocess import Popen, SubprocessError, TimeoutExpired, check_output
from time import monotonic, sleep
import traceback
from typing import TYPE_CHECKING

from ..project import ValidationError, load_project, workspace_format_notice
from ..sheets import sheet_source_files, workspace_root_for_sheet
from ..ui_settings import ZoomSettings

if TYPE_CHECKING:
    from ..windows_worker import WindowsWorker

def serve_flow(
    root: Path, zoom: ZoomSettings, publish: Callable[[dict], None], *,
    workspace_root: Path | None = None, run_token: str | None = None, main: bool = False,
) -> None:
    from ..web import serve

    try:
        workspace_format_notice(workspace_root or workspace_root_for_sheet(root))
        sources: dict[Path, str] = {}
        snapshot_sources = sheet_source_files(root, main=main) if run_token is not None else ()
        with load_project(root, sources, snapshot_sources=snapshot_sources) as flow:
            serve(
                flow, root, 0, sources, zoom=zoom, run_token=run_token,
                on_ready=lambda address, graph: publish({"url": address, "steps": graph, "output": flow.output.step_id}),
            )
    except ValidationError as error:
        publish({"error": str(error)})
    except Exception:
        if run_token is None:
            traceback.print_exc()
            publish({"error": "Could not open the flow. Check the Workspace terminal for details."})
        else:
            publish({"error": "Could not open the flow. Check the flow source and Workspace environment."})


@dataclass
class OpenFlow:
    process: Popen | WindowsWorker
    url: str = ""
    run_token: str | None = None
    lifeline: int | None = None

    def exited(self) -> bool:
        try:
            self.process.wait(timeout=2)
        except TimeoutExpired:
            return False
        if self.run_token is not None and os.name == "nt":
            self.process.stop()
        return True

    def stop(self) -> None:
        if self.run_token is not None and os.name == "nt":
            self.process.stop()
            return
        if self.run_token is None or os.name != "posix":
            terminate(self.process)
            return
        if self.lifeline is not None:
            os.close(self.lifeline)
            self.lifeline = None
        try:
            self.process.wait(timeout=4)
        except TimeoutExpired:
            raise OSError(f"Workspace process {self.process.pid} is still running.") from None


class WorkspaceProcessRunningError(RuntimeError):
    pass


def terminate(process: Popen) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=2)
    except TimeoutExpired:
        process.kill()
        process.wait()


def workspace_command_pattern(root: Path) -> re.Pattern[str]:
    candidates = dict.fromkeys((str(root), str(root.resolve())))
    if os.name == "nt":
        candidates = (candidate.replace("\\", "/") for candidate in candidates)
    joined = "|".join(re.escape(item) for item in candidates)
    flags = re.IGNORECASE if os.name == "nt" else 0
    separators = r"/\\" if os.name == "nt" else "/"
    return re.compile(rf"(?:^|[\s'\"=:])(?:{joined})(?=$|[{separators}\s'\":,])", flags)


def active_workspace_pids(root: Path, tracked_pids: tuple[int, ...] = ()) -> list[int]:
    if os.name == "nt":
        from ..windows_workspace import windows_process_commands

        pattern = workspace_command_pattern(root)
        resolved = root.resolve()
        try:
            return sorted({
                *tracked_pids,
                *(
                    pid for pid, command, executable in windows_process_commands()
                    if pid != os.getpid() and (
                        pattern.search(command.replace("\\", "/"))
                        or executable and Path(executable).is_relative_to(resolved)
                    )
                ),
            })
        except OSError as error:
            raise WorkspaceProcessRunningError(str(error)) from error
    if os.name != "posix":
        return list(dict.fromkeys(tracked_pids))
    active: list[int] = []
    seen: set[int] = set()
    for pid in tracked_pids:
        if pid > 0 and pid != os.getpid() and pid not in seen:
            try:
                os.kill(pid, 0)
            except OSError:
                continue
            seen.add(pid)
            active.append(pid)
    pattern = workspace_command_pattern(root)
    try:
        output = check_output(
            ["ps", "-axo", "pid=,stat=,command="],
            text=True,
            errors="replace",
            timeout=2,
        )
    except (OSError, SubprocessError):
        return active
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        stat, command = parts[1], parts[2]
        if pid == os.getpid() or pid in seen or stat.startswith("Z"):
            continue
        if pattern.search(command):
            seen.add(pid)
            active.append(pid)
    return active


def verify_workspace_processes_stopped(
    root: Path,
    tracked_processes: tuple[Popen | WindowsWorker, ...] = (),
    *,
    wait_seconds: float = 1.0,
) -> None:
    deadline = monotonic() + wait_seconds
    while True:
        tracked_pids = tuple(process.pid for process in tracked_processes if process.poll() is None)
        pids = active_workspace_pids(root, tracked_pids)
        if not pids:
            return
        if monotonic() >= deadline:
            detail = ", ".join(str(pid) for pid in pids)
            raise WorkspaceProcessRunningError(
                f"Could not delete {root.name} because a Workspace process is still running (PID {detail}). "
                "End the process or restart the host through the operating system before retrying Delete."
            )
        sleep(.05)
