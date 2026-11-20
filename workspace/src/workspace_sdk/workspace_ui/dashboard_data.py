from functools import lru_cache
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path

from ..dashboard import PANEL_ID_PATTERN
from ..evidence import checked_metrics
from ..history import RunHistory


RUN_FIELDS = ("status", "started_at", "finished_at", "duration_ms")


def dashboard_sources(declaration: str) -> list[dict]:
    manifest = json.loads(declaration)
    sources = manifest.get("sources", []) if isinstance(manifest, dict) else None
    if not isinstance(sources, list) or len(sources) > 32:
        raise ValueError("Declare up to 32 dashboard sources in bridge.json.")
    ids = set()
    for source in sources:
        if not isinstance(source, dict) or not {"id", "sheet"} <= source.keys():
            raise ValueError("Each dashboard source requires an ID and a sheet path.")
        source_id, sheet = source["id"], source["sheet"]
        if (not isinstance(source_id, str) or len(source_id) > 64 or not PANEL_ID_PATTERN.fullmatch(source_id)
                or source_id in ids or not isinstance(sheet, str) or not sheet or len(sheet) > 512):
            raise ValueError("Use unique dashboard source IDs and valid sheet paths.")
        ids.add(source_id)
        if source.get("mode", "latest") not in ("latest", "live"):
            raise ValueError("Dashboard sources use latest or live mode.")
        selectors = source.keys() - {"id", "sheet", "mode"}
        if selectors == {"field"} and source["field"] in RUN_FIELDS:
            continue
        if selectors == {"metric"}:
            metric = source["metric"]
            if (isinstance(metric, dict) and metric.keys() == {"step", "group", "name"}
                    and all(isinstance(value, str) and 0 < len(value) <= 48
                            for key, value in metric.items() if key != "step")
                    and isinstance(metric["step"], str) and 0 < len(metric["step"]) <= 512):
                continue
        raise ValueError("Each dashboard source selects one run field or one step metric.")
    return sources


@lru_cache(maxsize=128)
def latest_dashboard_run(root: Path, history_version: tuple[int, int, int]) -> dict | None:
    record = RunHistory(root).latest()
    if record is None:
        return None
    return {
        **{field: record.get(field) for field in ("id", *RUN_FIELDS)},
        "metrics": {step["id"]: step.get("metrics", {}) for step in record.get("steps", [])},
    }


def sheet_dashboard_run(sheet, mode: str) -> dict | None:
    if mode == "latest":
        directory = sheet.root / ".workspace" / "runs"
        if directory.resolve() != directory:
            raise ValueError("Invalid run history location.")
        try:
            info = directory.stat()
        except FileNotFoundError:
            return None
        return latest_dashboard_run(sheet.root, (info.st_dev, info.st_ino, info.st_mtime_ns))

    worker = sheet.worker
    if worker is None or worker.url is None or worker.process.poll() is not None:
        return None
    connection = HTTPConnection(worker.url.removeprefix("http://"), timeout=.5)
    try:
        connection.request("GET", "/dashboard-live")
        with connection.getresponse() as response:
            if response.status != 200:
                raise ValueError("Could not read live dashboard data.")
            return json.load(response)
    finally:
        connection.close()


def dashboard_snapshot(root: Path, sheets, sources: list[dict]) -> dict:
    available = {
        sheet.root.relative_to(root).as_posix(): sheet
        for sheet in sheets
        if sheet is not None and not sheet.closing and sheet.root.is_relative_to(root)
        and (sheet.root / "flow.py").is_file() and not (sheet.root / "flow.py").is_symlink()
        and sheet.root.resolve() == sheet.root
    }
    runs = {}
    results = []
    for source in sources:
        reference = source["sheet"]
        result = {"id": source["id"], "sheet": reference, "state": "sheet_unavailable", "value": None}
        results.append(result)
        sheet = available.get(reference)
        if sheet is None:
            continue
        result["sheet_name"] = sheet.title
        mode = source.get("mode", "latest")
        key = reference, mode
        try:
            if key not in runs:
                runs[key] = sheet_dashboard_run(sheet, mode)
            run = runs[key]
            result["state"] = "no_data"
            if run is None:
                continue
            timestamp = run.get("finished_at") or run.get("started_at")
            if "field" in source:
                value = run.get(source["field"])
            else:
                metric = source["metric"]
                group = run["metrics"].get(metric["step"], {}).get(metric["group"], {})
                value = checked_metrics(metric["group"], group).get(metric["name"])
                if mode == "live":
                    timestamp = run["metric_times"].get(metric["step"], {}).get(metric["group"], timestamp)
            result.update(run_id=run["id"], timestamp=timestamp, value=value)
            if value is not None:
                result["state"] = "available"
        except (OSError, HTTPException, KeyError, TypeError, ValueError):
            result["state"] = "source_unavailable"
    return {"scope": "workspace", "sources": results}
