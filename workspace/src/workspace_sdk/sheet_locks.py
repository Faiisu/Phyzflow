from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
from subprocess import Popen, TimeoutExpired
from tempfile import NamedTemporaryFile
from threading import RLock
from time import monotonic, time
from typing import TypeVar
from uuid import uuid4


HEARTBEAT_INTERVAL_SECONDS = 15
STALE_TIMEOUT_SECONDS = 45
MAX_TRACKED_SESSIONS = 128

T = TypeVar("T")


class SheetLockedError(RuntimeError):
    def __init__(self, message: str, *, snapshot: dict | None = None) -> None:
        super().__init__(message)
        self.snapshot = snapshot


def _close_entry(process: Popen, lifeline: int | None) -> None:
    if lifeline is not None:
        with suppress(OSError):
            os.close(lifeline)
    elif process.poll() is None:
        if os.name == "posix":
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGTERM)
        else:
            with suppress(OSError):
                process.terminate()
    if process.poll() is None:
        try:
            process.wait(timeout=4)
        except TimeoutExpired:
            if os.name == "posix":
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                with suppress(OSError):
                    process.kill()
            with suppress(TimeoutExpired):
                process.wait(timeout=2)


def _matches_viewer(session_id: str, viewer: str | set[str] | frozenset[str] | None) -> bool:
    if viewer is None:
        return False
    if isinstance(viewer, str):
        return session_id == viewer
    return session_id in viewer


class SheetEditLease:
    def __init__(self, sheet_root: Path) -> None:
        self.sheet_root = sheet_root
        self._lock = RLock()
        self.owned_processes: dict[str, list[tuple[Popen, int | None]]] = {}
        self.invalidated_sessions: set[str] = set()
        self.closed_sessions: set[str] = set()
        self._closing_sessions: set[str] = set()
        self._generation = 0

    def _state_dir(self, *, create: bool = False) -> Path:
        directory = self.sheet_root / ".workspace"
        if directory.is_symlink():
            raise ValueError("Workspace state must not be a symbolic link.")
        if create:
            directory.mkdir(mode=0o700 if os.name == "posix" else 0o777, parents=True, exist_ok=True)
            if directory.is_symlink():
                raise ValueError("Workspace state must not be a symbolic link.")
        return directory

    def _lock_path(self, *, create_dir: bool = False) -> Path:
        path = self._state_dir(create=create_dir) / "edit-lock.json"
        if path.is_symlink():
            raise ValueError("Edit lock state must not be a symbolic link.")
        return path

    def _sessions_path(self, *, create_dir: bool = False) -> Path:
        path = self._state_dir(create=create_dir) / "edit-lock-sessions.json"
        if path.is_symlink():
            raise ValueError("Edit lock history must not be a symbolic link.")
        return path

    def _clock_offset(self) -> float:
        path = self.sheet_root / ".workspace" / "edit-lock-now"
        if path.is_symlink() or not path.is_file():
            return 0.0
        try:
            return float(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return 0.0

    def _now(self) -> tuple[str, float]:
        offset = self._clock_offset()
        wall = time() + offset
        mono = monotonic() + offset
        iso = datetime.fromtimestamp(wall, tz=timezone.utc).isoformat()
        return iso, mono

    def _write_json_atomic(self, target: Path, payload: dict) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        with NamedTemporaryFile(dir=target.parent, prefix=".edit-lock-", delete=False) as stream:
            pending = Path(stream.name)
            try:
                stream.write(encoded)
                stream.close()
                if os.name == "posix":
                    pending.chmod(0o600)
                if target.is_symlink():
                    raise ValueError("Edit lock state must not be a symbolic link.")
                pending.replace(target)
            finally:
                pending.unlink(missing_ok=True)

    def _read_json(self, path: Path) -> dict | None:
        if path.is_symlink():
            raise ValueError("Edit lock state must not be a symbolic link.")
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_bytes())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _load_sessions(self) -> tuple[int, list[str], list[str]]:
        data = self._read_json(self._sessions_path())
        generation = self._generation
        closed: list[str] = []
        invalidated: list[str] = []
        if data is not None:
            raw_gen = data.get("generation")
            if isinstance(raw_gen, int):
                generation = max(generation, raw_gen)
            closed = [item for item in data.get("closed", []) if isinstance(item, str)]
            invalidated = [item for item in data.get("invalidated", []) if isinstance(item, str)]
        self._generation = generation
        self.closed_sessions.update(closed)
        self.invalidated_sessions.update(invalidated)
        return generation, closed, invalidated

    def _save_sessions(self, *, generation: int | None = None, closed_id: str | None = None, invalidated_id: str | None = None) -> None:
        current_gen, closed, invalidated = self._load_sessions()
        if generation is not None:
            current_gen = max(current_gen, generation)
        if closed_id is not None and closed_id not in closed:
            closed.append(closed_id)
        if invalidated_id is not None and invalidated_id not in invalidated:
            invalidated.append(invalidated_id)
        self._generation = current_gen
        target = self._sessions_path(create_dir=True)
        self._write_json_atomic(
            target,
            {
                "generation": current_gen,
                "closed": closed[-MAX_TRACKED_SESSIONS:],
                "invalidated": invalidated[-MAX_TRACKED_SESSIONS:],
            },
        )

    def _read_lock(self) -> dict | None:
        return self._read_json(self._lock_path())

    def _snapshot(self, record: dict | None, viewer_session_id: str | set[str] | frozenset[str] | None = None) -> dict:
        if record is None:
            return {
                "locked": False,
                "session_id": None,
                "owner": None,
                "principal_type": None,
                "acquired_at": None,
                "last_heartbeat": None,
                "stale": False,
                "disconnected": False,
                "owned_by_viewer": False,
                "has_unsaved_draft": False,
                "generation": self._generation,
            }
        _, now_mono = self._now()
        age = max(0.0, now_mono - float(record.get("last_heartbeat_mono", now_mono)))
        disconnected = bool(record.get("disconnected"))
        stale = disconnected or age >= STALE_TIMEOUT_SECONDS
        owned = _matches_viewer(record["session_id"], viewer_session_id)
        return {
            "locked": True,
            "session_id": record["session_id"] if owned else None,
            "owner": record["owner"],
            "principal_type": record["principal_type"],
            "acquired_at": record["acquired_at"],
            "last_heartbeat": record["last_heartbeat"],
            "stale": stale,
            "disconnected": disconnected,
            "owned_by_viewer": owned,
            "has_unsaved_draft": record.get("unsaved_draft") is not None,
            "generation": int(record.get("generation", self._generation)),
        }

    def status(self, viewer_session_id: str | set[str] | frozenset[str] | None = None) -> dict:
        with self._lock:
            self._load_sessions()
            record = self._read_lock()
            return self._snapshot(record, viewer_session_id)

    def acquire(
        self,
        principal: str,
        principal_type: str,
        owner: str,
        session_id: str | None = None,
    ) -> dict:
        if principal_type not in ("mcp", "web"):
            raise ValueError("principal_type must be 'mcp' or 'web'.")
        if not owner:
            raise ValueError("An owner name is required.")
        with self._lock:
            generation, _, _ = self._load_sessions()
            if session_id is not None and (
                session_id in self.invalidated_sessions
                or session_id in self.closed_sessions
                or session_id in self._closing_sessions
            ):
                raise SheetLockedError(
                    "This editing session is no longer active.",
                    snapshot=self._snapshot(self._read_lock()),
                )
            current = self._read_lock()
            if current is not None:
                if (
                    session_id is not None
                    and current["session_id"] == session_id
                    and current["principal"] == principal
                ):
                    now_iso, now_mono = self._now()
                    updated = {
                        **current,
                        "last_heartbeat": now_iso,
                        "last_heartbeat_mono": now_mono,
                        "disconnected": False,
                    }
                    self._write_json_atomic(self._lock_path(create_dir=True), updated)
                    return self._snapshot(updated, session_id)
                raise SheetLockedError(
                    f"This sheet is locked for editing by {current['owner']}.",
                    snapshot=self._snapshot(current),
                )
            new_session_id = session_id or uuid4().hex
            generation = max(generation, self._generation) + 1
            now_iso, now_mono = self._now()
            record = {
                "session_id": new_session_id,
                "principal": principal,
                "principal_type": principal_type,
                "owner": owner,
                "generation": generation,
                "acquired_at": now_iso,
                "last_heartbeat": now_iso,
                "last_heartbeat_mono": now_mono,
                "disconnected": False,
                "unsaved_draft": None,
            }
            self._save_sessions(generation=generation)
            self._write_json_atomic(self._lock_path(create_dir=True), record)
            return self._snapshot(record, new_session_id)

    def heartbeat(self, principal: str, session_id: str) -> dict:
        with self._lock:
            self._load_sessions()
            if (
                not session_id
                or session_id in self.invalidated_sessions
                or session_id in self.closed_sessions
                or session_id in self._closing_sessions
            ):
                raise SheetLockedError(
                    "This editing session is no longer active.",
                    snapshot=self._snapshot(self._read_lock()),
                )
            current = self._read_lock()
            if current is None or current["session_id"] != session_id or current["principal"] != principal:
                message = (
                    f"This sheet is locked for editing by {current['owner']}."
                    if current is not None
                    else "This editing session is not active."
                )
                raise SheetLockedError(message, snapshot=self._snapshot(current))
            now_iso, now_mono = self._now()
            updated = {
                **current,
                "last_heartbeat": now_iso,
                "last_heartbeat_mono": now_mono,
                "disconnected": False,
            }
            self._write_json_atomic(self._lock_path(create_dir=True), updated)
            return self._snapshot(updated, session_id)

    def mark_disconnected(self, session_id: str) -> dict | None:
        with self._lock:
            self._load_sessions()
            if (
                not session_id
                or session_id in self.invalidated_sessions
                or session_id in self.closed_sessions
                or session_id in self._closing_sessions
            ):
                return None
            current = self._read_lock()
            if current is None or current["session_id"] != session_id:
                return None
            updated = {**current, "disconnected": True}
            self._write_json_atomic(self._lock_path(create_dir=True), updated)
            return self._snapshot(updated)

    def register_process(self, session_id: str, process: Popen, lifeline: int | None = None) -> None:
        with self._lock:
            self._load_sessions()
            if (
                not session_id
                or session_id in self.invalidated_sessions
                or session_id in self.closed_sessions
                or session_id in self._closing_sessions
            ):
                raise SheetLockedError(
                    "This editing session is no longer active.",
                    snapshot=self._snapshot(self._read_lock()),
                )
            current = self._read_lock()
            if current is None or current["session_id"] != session_id:
                raise SheetLockedError(
                    "This editing session is not active.",
                    snapshot=self._snapshot(current),
                )
            self.owned_processes.setdefault(session_id, []).append((process, lifeline))

    def unregister_process(self, session_id: str, process: Popen) -> None:
        with self._lock:
            entries = self.owned_processes.get(session_id)
            if not entries:
                return
            remaining = []
            for entry_process, lifeline in entries:
                if entry_process is process:
                    if lifeline is not None:
                        with suppress(OSError):
                            os.close(lifeline)
                else:
                    remaining.append((entry_process, lifeline))
            if remaining:
                self.owned_processes[session_id] = remaining
            else:
                self.owned_processes.pop(session_id, None)

    def cleanup_processes(self, session_id: str) -> None:
        with self._lock:
            entries = self.owned_processes.pop(session_id, [])
        for process, lifeline in entries:
            _close_entry(process, lifeline)

    def cleanup_process(self, session_id: str | None, process: Popen, fallback_lifeline: int | None = None) -> None:
        lifeline = fallback_lifeline
        if session_id:
            with self._lock:
                entries = self.owned_processes.get(session_id)
                if entries:
                    remaining = []
                    found = False
                    for entry_process, entry_lifeline in entries:
                        if entry_process is process and not found:
                            found = True
                            lifeline = entry_lifeline
                        else:
                            remaining.append((entry_process, entry_lifeline))
                    if remaining:
                        self.owned_processes[session_id] = remaining
                    else:
                        self.owned_processes.pop(session_id, None)
                else:
                    lifeline = None
        _close_entry(process, lifeline)

    def release(self, principal: str, session_id: str, outcome: str = "succeeded") -> dict:
        if outcome not in ("succeeded", "failed", "cancelled"):
            raise ValueError("Outcome must be 'succeeded', 'failed', or 'cancelled'.")
        with self._lock:
            self._load_sessions()
            if (
                not session_id
                or session_id in self.invalidated_sessions
                or session_id in self.closed_sessions
                or session_id in self._closing_sessions
            ):
                raise SheetLockedError(
                    "This editing session is no longer active.",
                    snapshot=self._snapshot(self._read_lock()),
                )
            current = self._read_lock()
            if current is None or current["session_id"] != session_id or current["principal"] != principal:
                message = (
                    f"This sheet is locked for editing by {current['owner']}."
                    if current is not None
                    else "This editing session is not active."
                )
                raise SheetLockedError(message, snapshot=self._snapshot(current))
            self._closing_sessions.add(session_id)
            entries = self.owned_processes.pop(session_id, [])

        try:
            for process, lifeline in entries:
                _close_entry(process, lifeline)
        finally:
            with self._lock:
                self._closing_sessions.discard(session_id)

        with self._lock:
            self.closed_sessions.add(session_id)
            self._save_sessions(closed_id=session_id)
            current = self._read_lock()
            if current is not None and current["session_id"] == session_id:
                self._lock_path().unlink(missing_ok=True)
        return {"released": True, "outcome": outcome}

    def force_open(self) -> dict:
        with self._lock:
            self._load_sessions()
            current = self._read_lock()
            if current is None:
                return {"forced": False, "previous_owner": None, "generation": self._generation}
            old_session = current["session_id"]
            previous_owner = current["owner"]
            next_gen = max(self._generation, int(current.get("generation", 0))) + 1
            self.invalidated_sessions.add(old_session)
            self._closing_sessions.add(old_session)
            self._save_sessions(generation=next_gen, invalidated_id=old_session)
            entries = self.owned_processes.pop(old_session, [])
        try:
            for process, lifeline in entries:
                _close_entry(process, lifeline)
        finally:
            with self._lock:
                self._closing_sessions.discard(old_session)
                current = self._read_lock()
                if current is not None and current["session_id"] == old_session:
                    self._lock_path().unlink(missing_ok=True)
        return {"forced": True, "previous_owner": previous_owner, "generation": next_gen}

    def is_invalidated(self, session_id: str) -> bool:
        with self._lock:
            self._load_sessions()
            return session_id in self.invalidated_sessions

    def stage_draft(self, principal: str, session_id: str, path: str, source: str) -> dict:
        if not path:
            raise ValueError("A file path is required.")
        with self._lock:
            self.verify_write(principal, session_id or "")
            current = self._read_lock()
            assert current is not None
            drafts = dict(current.get("unsaved_draft") or {})
            drafts[path] = source
            updated = {**current, "unsaved_draft": drafts}
            self._write_json_atomic(self._lock_path(create_dir=True), updated)
            return self._snapshot(updated, session_id)

    def verify_write(self, principal: str, session_id: str | None) -> None:
        with self._lock:
            self._load_sessions()
            if session_id is not None and (
                session_id in self.invalidated_sessions
                or session_id in self.closed_sessions
                or session_id in self._closing_sessions
            ):
                raise SheetLockedError(
                    "This editing session is no longer active.",
                    snapshot=self._snapshot(self._read_lock()),
                )
            current = self._read_lock()
            if current is not None:
                if (
                    session_id is not None
                    and current["session_id"] == session_id
                    and current["principal"] == principal
                ):
                    return
                raise SheetLockedError(
                    f"This sheet is locked for editing by {current['owner']}.",
                    snapshot=self._snapshot(current),
                )
            if session_id is not None:
                raise SheetLockedError(
                    "This editing session is not active. Open the sheet for editing first.",
                    snapshot=self._snapshot(None),
                )

    def write_locked(
        self,
        principal: str,
        session_id: str | None,
        operation: Callable[[], T],
        *,
        path: str | None = None,
    ) -> T:
        with self._lock:
            self.verify_write(principal, session_id)
            result = operation()
            if path is not None and session_id is not None:
                current = self._read_lock()
                if current is not None and current["session_id"] == session_id:
                    drafts = current.get("unsaved_draft")
                    if isinstance(drafts, dict) and path in drafts:
                        remaining = {key: value for key, value in drafts.items() if key != path}
                        updated = {**current, "unsaved_draft": remaining or None}
                        self._write_json_atomic(self._lock_path(create_dir=True), updated)
            return result

    def close(self) -> None:
        with self._lock:
            all_entries = list(self.owned_processes.values())
            self.owned_processes.clear()
        for entries in all_entries:
            for process, lifeline in entries:
                _close_entry(process, lifeline)
