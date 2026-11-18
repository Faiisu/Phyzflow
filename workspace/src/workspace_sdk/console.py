"""Capture a sheet process's stdout and stderr for the active run."""

from codecs import getincrementaldecoder
from collections.abc import Callable
from contextlib import ExitStack, contextmanager, suppress
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from threading import Event, RLock, Thread
from time import monotonic, sleep
from uuid import uuid4


DEFAULT_SAVE_LOGS = True
DEFAULT_RETENTION_DAYS = 7
RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
SEGMENT_PATTERN = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})\.ndjson")


def _ensure_private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Console log storage must not be a symbolic link.")
    existed = path.exists()
    path.mkdir(parents=True, mode=0o700 if os.name == "posix" else 0o777, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Console log storage must not be a symbolic link.")
    if os.name == "posix" and not existed:
        os.chmod(path, 0o700)


def _write_private_bytes(path: Path, payload: bytes, flags: int) -> None:
    fd = os.open(path, flags, 0o600)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        view = memoryview(payload)
        written = 0
        while written < len(view):
            written += os.write(fd, view[written:])
    finally:
        os.close(fd)


def _validate_run_id(run_id: object) -> str:
    if type(run_id) is not str or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("Invalid run ID.")
    return run_id


def _validate_settings(save_logs: object, retention_days: object) -> dict[str, bool | int]:
    if type(save_logs) is not bool:
        raise ValueError("Save console logs must be true or false.")
    if type(retention_days) is not int or not 1 <= retention_days <= 3650:
        raise ValueError("Retention days must be a whole number between 1 and 3650.")
    return {"save_logs": save_logs, "retention_days": retention_days}


class ConsoleLogStore:
    def __init__(self, root: Path, clock: Callable[[], date] | None = None) -> None:
        self.root = root
        self.workspace_dir = root / ".workspace"
        self.settings_path = self.workspace_dir / "console-settings.json"
        self.logs_dir = self.workspace_dir / "logs"
        self._clock = clock
        self._lock = RLock()

    def current_date(self) -> date:
        if self._clock is not None:
            return self._clock()
        marker = self.workspace_dir / "log-date"
        for attempt in range(4):
            if marker.is_symlink():
                raise ValueError("Log date marker must not be a symbolic link.")
            try:
                raw = marker.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                break
            if raw:
                return date.fromisoformat(raw)
            if attempt < 3:
                sleep(0.005)
        env_date = os.environ.get("PHYZFLOW_LOG_DATE", "").strip()
        if env_date:
            return date.fromisoformat(env_date)
        return datetime.now(timezone.utc).date()

    def settings(self) -> dict[str, bool | int]:
        with self._lock:
            if self.settings_path.is_symlink():
                raise ValueError("Console log settings must not be a symbolic link.")
            try:
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {"save_logs": DEFAULT_SAVE_LOGS, "retention_days": DEFAULT_RETENTION_DAYS}
            if not isinstance(data, dict):
                raise ValueError("Console log settings must be a JSON object.")
            return _validate_settings(
                data.get("save_logs", DEFAULT_SAVE_LOGS),
                data.get("retention_days", DEFAULT_RETENTION_DAYS),
            )

    def update_settings(self, changes: object) -> dict[str, bool | int]:
        if not isinstance(changes, dict):
            raise ValueError("Send console log settings as a JSON object.")
        unknown = changes.keys() - {"save_logs", "retention_days"}
        if unknown or not changes:
            raise ValueError("Send save_logs or retention_days.")
        with self._lock:
            current = self.settings()
            updated = _validate_settings(
                changes.get("save_logs", current["save_logs"]),
                changes.get("retention_days", current["retention_days"]),
            )
            _ensure_private_dir(self.workspace_dir)
            if self.settings_path.is_symlink():
                raise ValueError("Console log settings must not be a symbolic link.")
            temporary = self.workspace_dir / f".console-settings.{uuid4().hex}.tmp"
            try:
                payload = json.dumps(updated, indent=2).encode("utf-8") + b"\n"
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                _write_private_bytes(temporary, payload, flags)
                temporary.replace(self.settings_path)
            finally:
                temporary.unlink(missing_ok=True)
            self.purge_expired()
            return updated

    def purge_expired(self) -> None:
        with self._lock:
            if self.logs_dir.is_symlink() or not self.logs_dir.is_dir():
                return
            retention_days = int(self.settings()["retention_days"])
            today = self.current_date()
            for run_dir in list(self.logs_dir.iterdir()):
                if run_dir.is_symlink() or not run_dir.is_dir() or RUN_ID_PATTERN.fullmatch(run_dir.name) is None:
                    continue
                for segment in list(run_dir.iterdir()):
                    if segment.is_symlink() or not segment.is_file():
                        continue
                    match = SEGMENT_PATTERN.fullmatch(segment.name)
                    if match is None:
                        continue
                    try:
                        segment_date = date.fromisoformat(match[1])
                    except ValueError:
                        continue
                    if (today - segment_date).days >= retention_days:
                        segment.unlink(missing_ok=True)
                try:
                    if not any(run_dir.iterdir()):
                        run_dir.rmdir()
                except FileNotFoundError:
                    pass

    def append(self, run_id: str, stream: str, text: str) -> None:
        validated_run_id = _validate_run_id(run_id)
        if stream not in ("stdout", "stderr") or not isinstance(text, str):
            raise ValueError("Console log stream must be stdout or stderr with text.")
        with self._lock:
            self.purge_expired()
            current = self.settings()
            if not current["save_logs"]:
                return
            today = self.current_date().isoformat()
            _ensure_private_dir(self.workspace_dir)
            _ensure_private_dir(self.logs_dir)
            run_dir = self.logs_dir / validated_run_id
            _ensure_private_dir(run_dir)
            segment = run_dir / f"{today}.ndjson"
            if segment.is_symlink():
                raise ValueError("Console log segment must not be a symbolic link.")
            line = json.dumps(
                {"run_id": validated_run_id, "stream": stream, "text": text},
                allow_nan=False,
            ).encode("utf-8") + b"\n"
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
            _write_private_bytes(segment, line, flags)

    def _read_run_dir(self, run_dir: Path) -> list[dict[str, str]]:
        if run_dir.is_symlink() or not run_dir.is_dir():
            return []
        segments = sorted(
            (
                path for path in run_dir.iterdir()
                if not path.is_symlink() and path.is_file() and SEGMENT_PATTERN.fullmatch(path.name) is not None
            ),
            key=lambda path: path.name,
        )
        events = []
        for segment in segments:
            for raw_line in segment.read_text(encoding="utf-8").splitlines():
                if not raw_line:
                    continue
                try:
                    item = json.loads(raw_line)
                except ValueError:
                    continue
                if (
                    isinstance(item, dict)
                    and RUN_ID_PATTERN.fullmatch(str(item.get("run_id", ""))) is not None
                    and item.get("stream") in ("stdout", "stderr")
                    and isinstance(item.get("text"), str)
                ):
                    events.append({
                        "kind": "console",
                        "run_id": item["run_id"],
                        "stream": item["stream"],
                        "text": item["text"],
                    })
        return events

    def _surviving_run_dirs(self) -> list[tuple[int, Path]]:
        if self.logs_dir.is_symlink() or not self.logs_dir.is_dir():
            return []
        found = []
        for run_dir in self.logs_dir.iterdir():
            if run_dir.is_symlink() or not run_dir.is_dir() or RUN_ID_PATTERN.fullmatch(run_dir.name) is None:
                continue
            segments = [
                path for path in run_dir.iterdir()
                if not path.is_symlink() and path.is_file() and SEGMENT_PATTERN.fullmatch(path.name) is not None
            ]
            if not segments:
                continue
            latest_mtime = max(path.stat().st_mtime_ns for path in (run_dir, *segments))
            found.append((latest_mtime, run_dir))
        found.sort(key=lambda item: item[0], reverse=True)
        return found

    def read_run(self, run_id: str | None = None) -> list[dict[str, str]]:
        with self._lock:
            self.purge_expired()
            if run_id is not None:
                validated = _validate_run_id(run_id)
                return self._read_run_dir(self.logs_dir / validated)
            surviving = self._surviving_run_dirs()
            if not surviving:
                return []
            return self._read_run_dir(surviving[0][1])

    def latest_events(self) -> list[dict[str, str]]:
        return self.read_run(None)


def flush_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            with suppress(OSError, ValueError):
                stream.flush()


@contextmanager
def capture_console(emit, on_tick=None):
    done = Event()
    drains = []
    flush_console()
    with ExitStack() as files, ExitStack() as redirects:
        for name, descriptor in (("stdout", 1), ("stderr", 2)):
            original = os.dup(descriptor)
            files.callback(os.close, original)
            reader, writer = os.pipe()
            files.callback(os.close, reader)
            try:
                os.set_blocking(reader, False)
                os.dup2(writer, descriptor)
                redirects.callback(os.dup2, original, descriptor)
            finally:
                os.close(writer)
            drains.append((name, reader, getincrementaldecoder("utf-8")(errors="replace")))

        def read_output():
            deadline = None
            draining = False
            last_tick = monotonic()
            while True:
                pending = False
                for name, descriptor, decoder in drains:
                    try:
                        data = os.read(descriptor, 4096)
                    except BlockingIOError:
                        continue
                    if data:
                        pending = True
                        text = decoder.decode(data)
                        if text:
                            emit(name, text)
                if on_tick is not None and monotonic() - last_tick >= .1:
                    last_tick = monotonic()
                    on_tick()
                if done.is_set():
                    if deadline is None:
                        deadline = monotonic() + .25
                    if draining and (not pending or monotonic() >= deadline):
                        break
                    draining = True
                elif not pending:
                    done.wait(.02)
            for name, _, decoder in drains:
                if text := decoder.decode(b"", final=True):
                    emit(name, text)

        reader_thread = Thread(target=read_output, name="sheet-console")
        reader_thread.start()
        try:
            yield
        finally:
            flush_console()
            redirects.close()
            done.set()
            reader_thread.join()
