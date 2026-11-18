from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from .project import relative_source


class RunHistory:
    def __init__(self, root: Path):
        self.root = root
        self.directory = root / ".workspace" / "runs"
        self.directory.mkdir(parents=True, mode=0o700 if os.name == "posix" else 0o777, exist_ok=True)

    def location(self, event: dict[str, Any]) -> dict[str, Any]:
        return {"file": relative_source(self.root, event["file"]), "line": event["line"]}

    def save(self, events: list[dict[str, Any]], loaded_sources: dict[Path, str], duration_ms: float) -> None:
        snapshot = loaded_sources.copy()
        source_paths = {str(path) for path in snapshot}
        sources = sorted((str(path.relative_to(self.root)), digest) for path, digest in snapshot.items())
        started, finished = events[0], events[-1]
        steps: dict[str, dict[str, Any]] = {}
        for event in events[1:-1]:
            step = steps.setdefault(event["step_id"], {
                "id": event["step_id"], "source": self.location(event),
            })
            step.update({
                key: event[key]
                for key in (
                    "status", "time", "duration_ms", "input", "output", "reasons", "metrics",
                    "input_sample", "output_sample",
                )
                if key in event
            })
            if event["status"] == "running":
                step["started_at"] = event["time"]
            elif event["status"] in ("succeeded", "failed", "stopped"):
                step["finished_at"] = event["time"]
            if "blocked_by" in event:
                step["blocked_by"] = event["blocked_by"]
            if "error_frames" in event:
                step["error_type"] = event["diagnostic_error_type"]
                step["error_frames"] = [
                    self.location(frame) for frame in event["error_frames"] if frame["file"] in source_paths
                ]

        record = {
            "id": started["run_id"],
            "status": finished["status"],
            "started_at": started["time"],
            "finished_at": finished["time"],
            "duration_ms": duration_ms,
            "source_version": sha256(json.dumps(sources).encode()).hexdigest(),
            "input": started["input"],
            "output": finished.get("output"),
            "steps": list(steps.values()),
        }
        temporary = self.directory / f".{record['id']}.{uuid4().hex}.tmp"
        try:
            temporary.write_bytes(json.dumps(record, allow_nan=False).encode())
            temporary.replace(self.directory / f"{record['id']}.json")
        finally:
            temporary.unlink(missing_ok=True)

    def list_runs(self) -> list[dict[str, Any]]:
        runs = []
        for path in self.directory.glob("*.json"):
            record = json.loads(path.read_bytes())
            runs.append({key: record[key] for key in ("id", "status", "started_at", "duration_ms")})
        return sorted(runs, key=lambda run: run["started_at"], reverse=True)

    def read(self, run_id: str) -> dict[str, Any] | None:
        if re.fullmatch(r"[0-9a-f]{32}", run_id):
            try:
                return json.loads((self.directory / f"{run_id}.json").read_bytes())
            except FileNotFoundError:
                pass
        return None
