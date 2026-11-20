from collections import deque
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import sqlite3
from threading import Condition, Lock
from uuid import uuid4


class RunStateStore:
    def __init__(self, state_dir: Path) -> None:
        state_dir.mkdir(mode=0o700 if os.name == "posix" else 0o777, parents=True, exist_ok=True)
        self.path = state_dir / "run_state.sqlite3"
        if os.name == "posix":
            if self.path.is_symlink():
                raise ValueError("Run state database must not be a symbolic link.")
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
            os.chmod(self.path, 0o600)
        self._lock = Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA synchronous=FULL")
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS sheet_states ("
                "workspace_id TEXT NOT NULL, "
                "sheet_path TEXT NOT NULL, "
                "generation INTEGER NOT NULL, "
                "run_id TEXT NOT NULL, "
                "status TEXT NOT NULL, "
                "replay_input TEXT, "
                "error TEXT, "
                "PRIMARY KEY (workspace_id, sheet_path))"
            )

            self._db.execute(
                "CREATE TABLE IF NOT EXISTS sheet_graphs ("
                "workspace_id TEXT NOT NULL, sheet_path TEXT NOT NULL, graph TEXT NOT NULL, "
                "PRIMARY KEY (workspace_id, sheet_path))"
            )

            self._db.execute(
                "CREATE TABLE IF NOT EXISTS visualize_layouts ("
                "workspace_id TEXT NOT NULL, sheet_path TEXT NOT NULL, panel_key TEXT NOT NULL, bounds TEXT NOT NULL, "
                "PRIMARY KEY (workspace_id, sheet_path, panel_key))"
            )

    def load_visualize_layout(self, workspace_id: str, sheet_path: str) -> dict:
        with self._lock:
            rows = self._db.execute(
                "SELECT panel_key, bounds FROM visualize_layouts WHERE workspace_id = ? AND sheet_path = ?",
                (workspace_id, sheet_path),
            ).fetchall()
        return {key: json.loads(bounds) for key, bounds in rows}

    def save_visualize_layout(self, workspace_id: str, sheet_path: str, panels: dict) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT INTO visualize_layouts (workspace_id, sheet_path, panel_key, bounds) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, sheet_path, panel_key) DO UPDATE SET bounds = excluded.bounds",
                ((workspace_id, sheet_path, key, json.dumps(bounds)) for key, bounds in panels.items()),
            )

    def record_graph(self, workspace_id: str, sheet_path: str, graph: dict) -> None:
        steps = [{**step, "description": ""} for step in graph["steps"]]
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO sheet_graphs (workspace_id, sheet_path, graph) VALUES (?, ?, ?)",
                (workspace_id, sheet_path, json.dumps({**graph, "steps": steps})),
            )

    def load_graph(self, workspace_id: str, sheet_path: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT graph FROM sheet_graphs WHERE workspace_id = ? AND sheet_path = ?",
                (workspace_id, sheet_path),
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def _record_state(
        self,
        workspace_id: str,
        sheet_path: str,
        generation: int,
        run_id: str,
        status: str,
        replay_input: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sheet_states (workspace_id, sheet_path, generation, run_id, status, replay_input, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, sheet_path) DO UPDATE SET "
                "generation = excluded.generation, "
                "run_id = excluded.run_id, "
                "status = excluded.status, "
                "replay_input = excluded.replay_input, "
                "error = excluded.error "
                "WHERE excluded.generation >= sheet_states.generation",
                (workspace_id, sheet_path, generation, run_id, status, replay_input, error),
            )

    def record_running(
        self,
        workspace_id: str,
        sheet_path: str,
        generation: int,
        run_id: str,
        inputs: dict | None,
    ) -> None:
        encoded = json.dumps(inputs) if inputs is not None else None
        self._record_state(workspace_id, sheet_path, generation, run_id, "running", replay_input=encoded)

    def record_stopped(self, workspace_id: str, sheet_path: str, generation: int, run_id: str) -> None:
        self._record_state(workspace_id, sheet_path, generation, run_id, "stopped")

    def record_terminal(
        self,
        workspace_id: str,
        sheet_path: str,
        generation: int,
        run_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        if status not in ("succeeded", "failed"):
            raise ValueError(f"Unsupported terminal run status: {status!r}")
        self._record_state(workspace_id, sheet_path, generation, run_id, status, error=error)

    def workspace_states(self, workspace_id: str) -> dict[str, dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT sheet_path, generation, run_id, status, replay_input, error "
                "FROM sheet_states WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall()
        return {
            sheet_path: {
                "generation": generation,
                "run_id": run_id,
                "status": status,
                "replay_input": json.loads(replay_input) if replay_input is not None else None,
                "error": error,
            }
            for sheet_path, generation, run_id, status, replay_input, error in rows
        }

    def active_workspace_ids(self) -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT DISTINCT workspace_id FROM sheet_states").fetchall()
        return [workspace_id for (workspace_id,) in rows]

    def delete_sheet(self, workspace_id: str, sheet_path: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM sheet_states WHERE workspace_id = ? AND sheet_path = ?",
                (workspace_id, sheet_path),
            )
            self._db.execute(
                "DELETE FROM sheet_graphs WHERE workspace_id = ? AND sheet_path = ?",
                (workspace_id, sheet_path),
            )
            self._db.execute(
                "DELETE FROM visualize_layouts WHERE workspace_id = ? AND sheet_path = ?",
                (workspace_id, sheet_path),
            )

    def delete_workspace(self, workspace_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sheet_states WHERE workspace_id = ?", (workspace_id,))
            self._db.execute("DELETE FROM sheet_graphs WHERE workspace_id = ?", (workspace_id,))
            self._db.execute("DELETE FROM visualize_layouts WHERE workspace_id = ?", (workspace_id,))

    def close(self) -> None:
        with self._lock:
            self._db.close()


class SheetRun:
    def __init__(
        self,
        inputs: dict | None,
        *,
        retry_count: int = 0,
        generation: int = 0,
        run_id: str | None = None,
        status: str = "running",
        start_error: str | None = None,
    ) -> None:
        self.run_id = run_id or uuid4().hex
        self.inputs = inputs
        self.retry_count = retry_count
        self.generation = generation
        self.crash_exhausted = False
        self.status = status
        self.start_error: str | None = start_error
        self._condition = Condition()
        self._lines: deque[bytes] = deque(maxlen=256)
        self._count = 0

    def publish(self, line: bytes) -> None:
        with self._condition:
            self._lines.append(line)
            self._count += 1
            self._condition.notify_all()

    def finish(self, status: str, error: str | None = None) -> None:
        with self._condition:
            if self.status != "running":
                return
            self.status = status
            self.start_error = error
            if error:
                self.publish(json.dumps({"run_id": self.run_id, "error": error}).encode() + b"\n")
            self._condition.notify_all()

    def snapshot_events(self) -> list[dict]:
        with self._condition:
            lines = list(self._lines)
        events = []
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                events.append(item)
        return events

    def messages(self):
        cursor = 0
        while True:
            with self._condition:
                self._condition.wait_for(lambda: cursor < self._count or self.status != "running")
                cursor = max(cursor, self._count - len(self._lines))
                if cursor == self._count:
                    return
                line = self._lines[cursor - self._count + len(self._lines)]
                cursor += 1
            yield line

    def collect(self, url: str, token: str) -> tuple[str | None, str | None]:
        connection = HTTPConnection(url.removeprefix("http://"), timeout=2)
        headers = {
            "Content-Type": "application/json",
            "X-Phyzflow-Worker-Token": token,
            "X-Phyzflow-Run-Id": self.run_id,
        }
        terminal = None
        reported = None
        try:
            connection.connect()
            socket = connection.sock
            connection.request("POST", "/run", body=json.dumps(self.inputs or {}).encode(), headers=headers)
            with connection.getresponse() as response:
                if response.status != 200:
                    return "failed", f"The flow could not start ({response.status})."
                socket.settimeout(None)
                for line in response:
                    message = json.loads(line)
                    self.publish(line)
                    if "result" in message:
                        reported = message["result"]["status"]
                    elif "error" in message:
                        reported = "failed"
                    elif "step_id" not in message and message.get("status") in ("succeeded", "failed"):
                        terminal = message["status"]
        except (OSError, ValueError, HTTPException):
            pass
        finally:
            connection.close()
        return reported or terminal, None
