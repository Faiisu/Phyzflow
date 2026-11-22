from functools import lru_cache
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
import re
from urllib.parse import urlencode

from ..evidence import checked_metrics
from ..execution import sheet_busy
from ..history import RunHistory


RUN_FIELDS = ("status", "started_at", "finished_at", "duration_ms")
STEP_FIELDS = ("id", *RUN_FIELDS)


@lru_cache(maxsize=128)
def latest_dashboard_run(root: Path, history_version: tuple[int, int, int]) -> dict | None:
    record = RunHistory(root).latest()
    if record is None:
        return None
    run = {
        **{field: record.get(field) for field in ("id", *RUN_FIELDS)},
        "steps": [
            {
                **{field: step[field] for field in STEP_FIELDS if field in step},
                "metrics": {
                    group: values
                    for group, raw in step.get("metrics", {}).items()
                    if (values := checked_metrics(group, raw))
                },
            }
            for step in record.get("steps", [])
        ],
    }
    source_version = record.get("source_version")
    if isinstance(source_version, str) and re.fullmatch(r"[0-9a-f]{64}", source_version):
        run["source_version"] = source_version
    return run


def saved_dashboard_run(root: Path) -> dict | None:
    directory = root / ".workspace" / "runs"
    if directory.resolve() != directory:
        raise ValueError("Invalid run history location.")
    try:
        info = directory.stat()
    except FileNotFoundError:
        return None
    return latest_dashboard_run(root, (info.st_dev, info.st_ino, info.st_mtime_ns))


def _sheet_may_be_running(sheet) -> bool:
    attempt = getattr(sheet, "attempt", None)
    if attempt is not None and getattr(attempt, "status", None) == "running":
        return True
    if getattr(sheet, "_cached_worker_status", None) not in ("ready", "succeeded", "failed"):
        return True
    return sheet_busy(sheet.root)


def read_live_dashboard(sheet, path: str) -> dict | None:
    worker = sheet.worker
    if worker is None or not worker.url or worker.process.poll() is not None or not _sheet_may_be_running(sheet):
        return None
    connection = HTTPConnection(worker.url.removeprefix("http://"), timeout=.5)
    try:
        connection.request("GET", path)
        with connection.getresponse() as response:
            if response.status != 200:
                raise ValueError("Could not read live dashboard data.")
            data = json.load(response)
            return data if sheet.worker is worker and not sheet.closing else None
    finally:
        connection.close()


def placement_snapshot(sheet, placement: dict) -> dict:
    node_id = placement["scope"]
    node_query = "" if node_id is None else "?" + urlencode({"node": node_id})
    try:
        live = read_live_dashboard(sheet, f"/dashboard-live-placement{node_query}")
    except (OSError, HTTPException, ValueError):
        live = None

    saved = None if live is not None else saved_dashboard_run(sheet.root)
    if saved is not None and node_id is not None:
        saved = {**saved, "steps": [step for step in saved["steps"] if step["id"] == node_id]}
    run = live["run"] if live is not None else saved
    return {
        "scope": "workflow" if node_id is None else "node",
        "nodeId": node_id,
        "panelId": placement["panel_id"],
        "sheet": placement["sheet"],
        "sheetName": sheet.title,
        "mode": "saved" if saved is not None else "live",
        "timestamp": (run.get("finished_at") or run["started_at"]) if run is not None else None,
        "graph": [
            {"id": step["id"], "dependencies": step.get("dependencies", []) if node_id is None else []}
            for step in (sheet.recovery_graph or {}).get("steps", [])
            if node_id is None or step["id"] == node_id
        ],
        "run": run,
        "previews": {},
        "liveFrames": live["liveFrames"] if live is not None else {},
        "liveMetrics": live["liveMetrics"] if live is not None else {},
        "settings": {},
        "settingChanges": [],
    }
