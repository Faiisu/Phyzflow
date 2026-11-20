from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack, suppress
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from subprocess import Popen
import sys
from tempfile import TemporaryDirectory
from threading import RLock, Thread
from time import monotonic, sleep
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from ..agent_supervisor import spawn_supervised
from ..execution import FlowBusy, sheet_busy, sheet_lock
from ..project import workspace_format_notice
from ..sheet_locks import SheetEditLease
from ..sheet_runs import RunStateStore, SheetRun
from ..sheets import (
    declared_graph,
    editable_sheet_python_files,
    source_stamp,
)
from ..ui_settings import ZoomSettings
from ..web import sample_input
from .processes import OpenFlow


MAX_CRASH_RETRIES = 3
SUPPORTED_SHEET_WORKER_PROTOCOL_VERSIONS = (1,)


class ReloadConflict(RuntimeError):
    pass


class ReloadDeferred(RuntimeError):
    pass


def _worker_reply(path: Path, worker: OpenFlow, deadline: float) -> dict:
    while not path.is_file():
        if worker.process.poll() is not None and not path.is_file():
            raise RuntimeError("The flow process exited during startup.")
        if monotonic() >= deadline:
            raise RuntimeError("The flow did not start within ten seconds.")
        sleep(.02)
    try:
        reply = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise RuntimeError("The Sheet Worker startup reply contained invalid JSON.") from error
    if not isinstance(reply, dict):
        raise RuntimeError("The Sheet Worker startup reply must be a JSON object.")
    return reply


class FlowSheet:
    def __init__(
        self, title: str, root: Path, zoom: ZoomSettings, *,
        workspace_root: Path, main: bool = False, interpreter: Path | None = None,
        workspace_closing: Callable[[], bool] = lambda: False,
        workspace_id: str | None = None,
        sheet_key: str | None = None,
        run_state: RunStateStore | None = None,
    ):
        self.title = title
        self.root = root
        self.workspace_root = workspace_root
        self.zoom = zoom
        self.worker: OpenFlow | None = None
        self.startup: dict | None = None
        self.recovery_graph: dict | None = None
        self.inactive_status = "ready"
        self.revision = 0
        self.closing = False
        self.lock = RLock()
        self.main = main
        self.interpreter = interpreter
        self.workspace_closing = workspace_closing
        self.workspace_id = workspace_id
        self.sheet_key = sheet_key
        self.run_state = run_state
        if run_state is not None and workspace_id is not None and sheet_key is not None:
            self.recovery_graph = run_state.load_graph(workspace_id, sheet_key)
        self.run_generation = 0
        self.attempted_source: str | None = None
        self.attempt: SheetRun | None = None
        self.edit_lease = SheetEditLease(self.root)
        self._cached_worker_status: str | None = None
        self._source_dirs: tuple[tuple[Path, int, int], ...] | None = None
        self._source_files: tuple[Path, ...] = ()
        self._source_entries: list[tuple[str, int, int, int, int]] | None = None
        self._source_digest: str | None = None

    def _dirs_unchanged(self) -> bool:
        if self._source_dirs is None:
            return False
        for directory, ino, mtime_ns in self._source_dirs:
            try:
                stat = directory.stat(follow_symlinks=False)
            except OSError:
                return False
            if stat.st_ino != ino or stat.st_mtime_ns != mtime_ns:
                return False
        return True

    def _scan_source_tree(self) -> tuple[tuple[tuple[Path, int, int], ...], tuple[Path, ...]]:
        dirs = []
        files = []
        for directory, folders, filenames in os.walk(self.root):
            path = Path(directory)
            folders[:] = [
                name for name in folders
                if not name.startswith(".") and name != "__pycache__"
                and not (self.main and path == self.root and name == "flows")
                and not (path / name / "pyvenv.cfg").is_file()
            ]
            try:
                dir_stat = path.stat(follow_symlinks=False)
                dirs.append((path, dir_stat.st_ino, dir_stat.st_mtime_ns))
            except OSError:
                pass
            for name in filenames:
                if name.endswith(".py") or (path == self.root and name == "pyproject.toml"):
                    files.append(path / name)
        return tuple(dirs), tuple(files)

    def _current_source_stamp(self) -> str:
        if not self._dirs_unchanged():
            self._source_dirs, self._source_files = self._scan_source_tree()
            self._source_entries = None
        entries = []
        for source in self._source_files:
            try:
                stat = source.stat(follow_symlinks=False)
            except OSError:
                self._source_dirs = None
                return source_stamp(self.root, main=self.main)
            entries.append((
                str(source.relative_to(self.root)),
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            ))
        entries.sort()
        if entries == self._source_entries and self._source_digest is not None:
            return self._source_digest
        digest = sha256(json.dumps(entries).encode()).hexdigest()
        self._source_entries = entries
        self._source_digest = digest
        return digest

    def edit_lock_status(self, viewer_sessions) -> dict:
        ws_dir = self.root / ".workspace"
        lock_file = ws_dir / "edit-lock.json"
        sessions_file = ws_dir / "edit-lock-sessions.json"
        if (
            not ws_dir.is_symlink()
            and not lock_file.is_symlink()
            and not sessions_file.is_symlink()
            and not lock_file.exists()
            and not sessions_file.exists()
        ):
            return {
                "locked": False,
                "session_id": None,
                "principal": None,
                "principal_type": None,
                "owner": None,
                "last_heartbeat": None,
                "stale": False,
                "disconnected": False,
                "owned_by_viewer": False,
                "has_unsaved_draft": False,
                "generation": self.edit_lease._generation,
            }
        return self.edit_lease.status(viewer_sessions)

    def changed_source(self, status: str) -> str | None:
        attempted = self.attempted_source
        if attempted is None or status == "stopped":
            return None
        stamp = self._current_source_stamp()
        return stamp if stamp != attempted else None

    def open(
        self, *, reload: bool = False, automatic_source: str | None = None, attempt: SheetRun | None = None,
    ) -> int:
        with self.lock, ExitStack() as reservation:
            if self.closing or self.workspace_closing():
                raise RuntimeError("Workspace is shutting down.")
            stopped = self.inactive_status == "stopped"
            replacing = False
            if reload:
                active = self.attempt
                if active is not None and active is not attempt and active.status == "running":
                    raise ReloadConflict("This flow is running. Wait for it to finish or stop it before reloading.")
                status = self.worker_status(probe=True)
                try:
                    reservation.enter_context(sheet_lock(self.root))
                except FlowBusy as error:
                    raise ReloadConflict(str(error)) from None
                if status == "running":
                    raise ReloadConflict("This flow is running. Wait for it to finish or stop it before reloading.")
                if status == "unavailable" and self.worker is not None and self.worker.process.poll() is None:
                    raise ReloadConflict("Could not verify the run status. Try reloading again or stop the flow first.")
                if automatic_source is not None and self.changed_source(status) != automatic_source:
                    raise ReloadDeferred
                workspace_format_notice(self.workspace_root)
                self.stop_worker(status="opening")
            if self.worker is not None:
                if (
                    self.worker.url
                    and self.worker.process.poll() is None
                    and self.inactive_status not in ("stopping", "unavailable")
                ):
                    return self.revision
                active = self.attempt
                if active is not None and active is not attempt and active.status == "running":
                    raise ReloadConflict("The flow is still running. Wait for its worker to recover or stop the flow.")
                self.stop_worker(status="opening")
                replacing = self.interpreter is not None

            active = self.attempt
            if attempt is None and active is not None and active.status != "running" and (stopped or reload):
                self.attempt = None
            self.attempted_source = automatic_source or self._current_source_stamp()
            self.inactive_status = "opening"
            try:
                self.start_worker()
            except BaseException:
                if reload:
                    self.revision += 1
                self.inactive_status = "unavailable"
                raise
            self._source_dirs = None
            self.attempted_source = automatic_source or self._current_source_stamp()
            if automatic_source is not None:
                self._current_source_stamp()
            if reload or replacing:
                self.revision += 1
            self.inactive_status = "ready"
            return self.revision

    def start_run(self, inputs: dict | None, *, revision: int | None = None) -> SheetRun:
        attempt = self.attempt
        if attempt is not None and attempt.status == "running":
            raise FlowBusy("A run is already in progress on this sheet.")
        with self.lock:
            if self.closing or self.workspace_closing():
                raise RuntimeError("Workspace is shutting down.")
            attempt = self.attempt
            if (attempt is not None and attempt.status == "running") or sheet_busy(self.root):
                raise FlowBusy("A run is already in progress on this sheet.")
            if revision is not None and revision != self.revision:
                raise ReloadConflict("The flow has been reloaded. Refresh its sheet.")
            return self._start_attempt(inputs)

    def _start_attempt(self, inputs: dict | None, *, retry_count: int = 0) -> SheetRun:
        self.run_generation += 1
        attempt = SheetRun(inputs, retry_count=retry_count, generation=self.run_generation)
        if self.run_state is not None and self.workspace_id is not None and self.sheet_key is not None:
            self.run_state.record_running(
                self.workspace_id, self.sheet_key, attempt.generation, attempt.run_id, inputs,
            )
        self.attempt = attempt
        Thread(target=self.collect_run, args=(attempt,), daemon=True).start()
        return attempt

    def collect_run(self, attempt: SheetRun) -> None:
        crashed = False
        try:
            with self.lock:
                if self.closing or self.workspace_closing() or self.attempt is not attempt or attempt.status != "running":
                    return
                worker = self.worker
                reload = (
                    worker is not None and worker.url and worker.process.poll() is None
                    and self.changed_source(self.inactive_status) is not None
                )
                if attempt.inputs is None:
                    attempt.inputs = sample_input(self.root)
                self.open(reload=bool(reload), attempt=attempt)
                worker = self.worker
            status, error = attempt.collect(worker.url, worker.run_token)
            if status is None:
                crashed = worker.exited()
                status = "failed"
                error = "The flow connection ended without reporting a result."
        except (OSError, RuntimeError, ValueError) as failure:
            status, error = "failed", str(failure)
        with self.lock:
            if self.attempt is not attempt or attempt.status != "running" or self.closing or self.workspace_closing():
                return
            if crashed and self.worker is worker:
                if attempt.retry_count < MAX_CRASH_RETRIES:
                    self._start_attempt(attempt.inputs, retry_count=attempt.retry_count + 1)
                    attempt.finish("failed")
                    return
                attempt.crash_exhausted = True
                error = f"The flow process exited after {MAX_CRASH_RETRIES} automatic restart attempts."
            attempt.finish(status, error)
            if self.run_state is not None and self.workspace_id is not None and self.sheet_key is not None:
                self.run_state.record_terminal(
                    self.workspace_id, self.sheet_key, attempt.generation, attempt.run_id, status, error,
                )

    def start_worker(self) -> None:
        self.startup = None
        with TemporaryDirectory(prefix="phyzflow-sheet-") as directory:
            startup = Path(directory) / "startup.json"
            worker = Path(__file__).parent.parent / "sheet_worker.py"
            argv = [self.interpreter or sys.executable, "-Pu", worker, "--start", startup, json.dumps(self.zoom)]
            options = {}
            token = None
            if self.interpreter is not None:
                argv.insert(1, "-I")
                token = uuid4().hex
                options["env"] = {
                    **os.environ,
                    "PATH": f"{self.interpreter.parent}{os.pathsep}{os.environ.get('PATH', '')}",
                    "VIRTUAL_ENV": str(self.interpreter.parent.parent),
                }
            argv.extend((token or "", str(int(self.main)), self.workspace_root))
            lifeline = None
            if token is not None and os.name == "posix":
                process, lifeline = spawn_supervised(argv, cwd=self.root, **options)
            elif token is not None and os.name == "nt":
                from ..windows_worker import WindowsWorker

                process = WindowsWorker(argv, cwd=self.root, **options)
            else:
                process = Popen(argv, cwd=self.root, **options)
            worker = self.worker = OpenFlow(process, run_token=token, lifeline=lifeline)
            try:
                deadline = monotonic() + 10
                bootstrap_error = None
                try:
                    reply = _worker_reply(startup, worker, deadline)
                except (OSError, RuntimeError) as error:
                    bootstrap_error = error
                    reply = {}
                protocol = reply.get("protocol_version")
                supported = SUPPORTED_SHEET_WORKER_PROTOCOL_VERSIONS
                self.startup = {
                    "stage": "startup",
                    "protocol_version": protocol,
                    "supported_protocol_versions": supported,
                    "loaded_sdk_version": reply.get("loaded_sdk_version"),
                    "installed_sdk_version": reply.get("installed_sdk_version"),
                }
                if type(protocol) is not int or protocol not in supported:
                    problem = "Sheet Worker protocol is missing" if protocol is None else f"Unsupported Sheet Worker protocol {protocol!r}"
                    versions = ", ".join(map(str, supported))
                    error = f"{problem}. Supported versions: {versions}."
                    if bootstrap_error is not None:
                        error = f"{error} {bootstrap_error}"
                    self.startup["error"] = error
                    raise RuntimeError(error) from bootstrap_error
                try:
                    startup.with_name("approved").touch()
                    reply = _worker_reply(startup.with_name("ready.json"), worker, deadline)
                    if "error" in reply:
                        raise RuntimeError(reply["error"])
                except (OSError, RuntimeError) as error:
                    self.startup["error"] = str(error)
                    raise
                worker.url = reply["url"]
                self._cached_worker_status = "ready"
                self.recovery_graph = {
                    "name": self.root.name, "steps": reply["steps"], "output": reply["output"], "zoom": self.zoom,
                }
                if self.run_state is not None and self.workspace_id is not None and self.sheet_key is not None:
                    with suppress(sqlite3.Error):
                        self.run_state.record_graph(self.workspace_id, self.sheet_key, self.recovery_graph)
            except BaseException:
                self.stop_worker(status="unavailable")
                raise

    def stop(self, *, shutdown: bool = False) -> None:
        with self.lock:
            if shutdown:
                self.closing = True
                self.edit_lease.close()
            else:
                self.run_generation += 1
                if self.run_state is not None and self.workspace_id is not None and self.sheet_key is not None:
                    self.run_state.record_stopped(
                        self.workspace_id,
                        self.sheet_key,
                        self.run_generation,
                        self.attempt.run_id if self.attempt else "",
                    )
            self.stop_run()

    def stop_run(self) -> None:
        if self.interpreter is not None:
            self.inactive_status = "stopping"
        if self.attempt is not None:
            self.attempt.finish("stopped")
        self.stop_worker()
        if self.interpreter is not None:
            self.revision += 1

    def stop_worker(self, *, status: str = "stopped") -> None:
        self._cached_worker_status = None
        if self.worker is not None:
            self.inactive_status = "stopping"
            try:
                self.worker.stop()
            except OSError:
                self.inactive_status = "unavailable"
                raise
            self.worker = None
        self.inactive_status = status

    def archive(self, destination: Path) -> None:
        with self.lock:
            self.edit_lease.close()
            self.stop_run()
            self.root.rename(destination)
            self.closing = True
            if self.run_state is not None and self.workspace_id is not None and self.sheet_key is not None:
                self.run_state.delete_sheet(self.workspace_id, self.sheet_key)

    def status(self) -> str:
        attempt = self.attempt
        if self.inactive_status == "stopping":
            return "stopping"
        if self.inactive_status == "unavailable" and self.interpreter is not None and self.worker is None:
            return attempt.status if attempt is not None else "ready"
        if self.inactive_status == "unavailable" and (
            attempt is None
            or (attempt.status != "running" and not attempt.crash_exhausted and attempt.start_error is None)
        ):
            return "unavailable"
        if attempt is not None and (attempt.status == "running" or self.inactive_status != "stopped"):
            return attempt.status
        return self.worker_status()

    def worker_ready(self) -> bool:
        worker = self.worker
        return bool(worker is not None and worker.url and worker.process.poll() is None)

    def ensure_worker_url(self, *, timeout: float = 10.0) -> str:
        deadline = monotonic() + timeout
        while True:
            with self.lock:
                status = self.status()
                worker = self.worker
                if status == "stopping":
                    raise RuntimeError("The flow is stopping. Try again after it stops.")
                if status != "running":
                    if worker is None or not worker.url or worker.process.poll() is not None:
                        self.open(reload=False)
                    elif self.changed_source(status) is not None:
                        self.open(reload=True)
                    worker = self.worker
                if worker is not None and worker.url and worker.process.poll() is None:
                    return worker.url
                if status != "running" or monotonic() >= deadline:
                    raise RuntimeError("Could not open the flow worker.")
            sleep(0.02)

    def source_graph(self) -> dict:
        files = sorted(editable_sheet_python_files(self.root, main=self.main))
        paths = [path.relative_to(self.root) for path in files]
        steps = declared_graph(self.root, {path.as_posix() for path in paths})
        if not steps:
            steps = [
                {"id": path.parent.as_posix() if path.name == "entrypoint.py" else path.as_posix(),
                 "source": path.as_posix(), "file": path.as_posix(), "description": "",
                 "dependencies": [], "arguments": []}
                for path in paths if path.as_posix() == "flow.py" or (
                    len(path.parts) == 3 and path.parts[0] == "nodes" and path.name == "entrypoint.py"
                )
            ]
        return {"name": self.root.name, "steps": steps, "zoom": self.zoom}

    def worker_status(self, *, probe: bool = False) -> str:
        if not self.closing and sheet_busy(self.root):
            self._cached_worker_status = None
            return "running"
        worker = self.worker
        if worker is None:
            return self.inactive_status
        if worker.url and worker.process.poll() is None:
            if not probe and self._cached_worker_status is not None:
                return self._cached_worker_status
            try:
                with build_opener(ProxyHandler({})).open(f"{worker.url}/status", timeout=.5) as response:
                    reported = json.load(response)["status"]
                    if reported != "running":
                        self._cached_worker_status = reported
                    return reported
            except (OSError, ValueError):
                self._cached_worker_status = None
        return "unavailable"
