from functools import lru_cache
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
import re
from urllib.parse import urlencode

from ..dashboard import MAX_WORKSPACE_FRAME_SOURCES, PANEL_ID_PATTERN
from ..evidence import checked_metrics
from ..history import RunHistory
from ..sheets import find_sheet_by_path


RUN_FIELDS = ("status", "started_at", "finished_at", "duration_ms")
STEP_FIELDS = ("id", *RUN_FIELDS)


def dashboard_sources(declaration: str) -> list[dict]:
    manifest = json.loads(declaration)
    sources = manifest.get("sources", []) if isinstance(manifest, dict) else None
    if not isinstance(sources, list) or len(sources) > 32:
        raise ValueError("Declare up to 32 dashboard sources in bridge.json.")
    ids = set()
    frame_count = 0
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
        if selectors == {"frame"}:
            frame = source["frame"]
            if (source.get("mode") != "live" or not isinstance(frame, dict) or frame.keys() != {"step"}
                    or not isinstance(frame["step"], str) or not 0 < len(frame["step"]) <= 512):
                raise ValueError("A dashboard frame source requires live mode and a step name.")
            frame_count += 1
            if frame_count > MAX_WORKSPACE_FRAME_SOURCES:
                raise ValueError(f"Declare up to {MAX_WORKSPACE_FRAME_SOURCES} frame sources per dashboard panel.")
            continue
        raise ValueError("Each dashboard source selects one run field, step metric, or live frame.")
    return sources


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


def sheet_dashboard_run(sheet, mode: str) -> dict | None:
    if mode == "latest":
        run = saved_dashboard_run(sheet.root)
        return None if run is None else {**run, "metrics": {step["id"]: step["metrics"] for step in run["steps"]}}
    return read_live_dashboard(sheet, "/dashboard-live")


def read_live_dashboard(sheet, path: str) -> dict | None:
    worker = sheet.worker
    if worker is None or not worker.url or worker.process.poll() is not None:
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


def _available_sheets(sheets) -> dict[Path, object]:
    return {
        sheet.root: sheet
        for sheet in sheets
        if sheet is not None and not sheet.closing
    }


def _resolve_sources(root: Path, available: dict[Path, object], runs: dict, sources: list[dict]) -> tuple[list[dict], bool]:
    frame_steps = {}
    for source in sources:
        if "frame" in source:
            frame_steps.setdefault(source["sheet"], set()).add(source["frame"]["step"])
    results = []
    live_active = False
    for source in sources:
        reference = source["sheet"]
        result = {"id": source["id"], "sheet": reference, "state": "sheet_unavailable", "value": None}
        results.append(result)
        sheet = available.get(find_sheet_by_path(root, reference))
        if sheet is None:
            continue
        result["sheet_name"] = sheet.title
        mode = source.get("mode", "latest")
        key = reference, "frames" if "frame" in source else mode
        try:
            if key not in runs:
                if "frame" in source:
                    query = urlencode({"step": sorted(frame_steps[reference])}, doseq=True)
                    runs[key] = read_live_dashboard(sheet, f"/dashboard-live-frames?{query}")
                else:
                    runs[key] = sheet_dashboard_run(sheet, mode)
            run = runs[key]
            if mode == "live" and run is not None:
                live_active = True
            result["state"] = "no_data"
            if run is None:
                continue
            timestamp = run.get("finished_at") or run.get("started_at")
            if "field" in source:
                value = run.get(source["field"])
            elif "frame" in source:
                frame = run["frames"].get(source["frame"]["step"])
                value = None
                if frame is not None:
                    value = f"data:image/jpeg;base64,{frame['jpeg']}"
                    timestamp = frame["time"]
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
    return results, live_active


def dashboard_snapshot(root: Path, sheets, sources: list[dict]) -> dict:
    available = _available_sheets(sheets)
    results, _ = _resolve_sources(root, available, {}, sources)
    return {"scope": "workspace", "sources": results}


def workspace_dashboard_batch(
    root: Path,
    sheets,
    panel_sources: dict[str, list[dict]],
    panel_errors: dict[str, str] | None = None,
) -> dict:
    available = _available_sheets(sheets)
    runs = {}
    panels = {}
    live_active = False
    for panel_id, sources in panel_sources.items():
        results, panel_live = _resolve_sources(root, available, runs, sources)
        live_active = live_active or panel_live
        panels[panel_id] = {"scope": "workspace", "sources": results}
    payload = {"scope": "workspace", "live_active": live_active, "panels": panels}
    if panel_errors:
        payload["errors"] = panel_errors
    return payload
