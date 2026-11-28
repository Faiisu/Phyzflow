import json
import re
from http.client import HTTPConnection, HTTPException
from typing import Annotated, Any, Literal

from ..console import ConsoleLogStore
from ..execution import FlowBusy
from ..history import RunHistory
from .access import WorkspaceAccess, ensure_not_protected, resolve_workspace_sheet

RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def _ensure_worker_url(flow_sheet) -> str:
    from mcp.server.mcpserver.exceptions import ToolError

    from ..workspace_ui import ReloadConflict

    try:
        return flow_sheet.ensure_worker_url()
    except (ReloadConflict, OSError, RuntimeError, ValueError, EOFError) as error:
        raise ToolError(str(error) or "Could not open the flow worker.") from None


def _worker_json(flow_sheet, method: str, path: str, payload: dict | None = None) -> dict:
    from mcp.server.mcpserver.exceptions import ToolError

    url = _ensure_worker_url(flow_sheet)
    body = None
    headers = {}
    if payload is not None:
        try:
            body = json.dumps(payload, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ToolError(str(error) or "Invalid JSON request payload.") from None
        headers["Content-Type"] = "application/json"

    connection = HTTPConnection(url.removeprefix("http://"), timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers)
        with connection.getresponse() as response:
            raw = response.read()
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
            if response.status >= 400:
                message = data.get("error") if isinstance(data, dict) else None
                raise ToolError(message or f"Flow worker returned {response.status}.")
            if not isinstance(data, dict):
                raise ToolError("Flow worker returned an unexpected response.")
            return data
    except (OSError, HTTPException) as error:
        raise ToolError(f"Could not reach the flow worker: {error}") from None
    finally:
        connection.close()


def _live_run_events(flow_sheet, run_id: str) -> list[dict]:
    worker = flow_sheet.worker
    if worker is None or not worker.url or worker.process.poll() is not None:
        return []
    connection = HTTPConnection(worker.url.removeprefix("http://"), timeout=2)
    try:
        connection.request("GET", "/live-run")
        with connection.getresponse() as response:
            if response.status != 200:
                return []
            payload = json.loads(response.read())
    except (OSError, HTTPException, ValueError):
        return []
    finally:
        connection.close()
    items = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    events = []
    for entry in items:
        if (
            isinstance(entry, (list, tuple))
            and len(entry) == 2
            and isinstance(entry[1], dict)
            and entry[1].get("run_id") == run_id
        ):
            events.append(entry[1])
    return events


def _summarize_attempt(attempt, flow_sheet) -> dict:
    events = attempt.snapshot_events()
    if attempt.status == "running":
        live_events = _live_run_events(flow_sheet, attempt.run_id)
        if live_events:
            events = live_events + [
                event for event in events if event.get("kind") != "console"
            ]
    steps: dict[str, dict] = {}
    result = None
    error = attempt.start_error
    started_at = None
    finished_at = None
    for event in events:
        if event.get("kind") == "console":
            continue
        if "result" in event:
            result = event["result"]
        if "error" in event and isinstance(event["error"], str):
            error = event["error"]
        step_id = event.get("step_id")
        if not isinstance(step_id, str):
            status = event.get("status")
            if status == "running" and started_at is None:
                started_at = event.get("time")
            elif status in ("succeeded", "failed", "stopped"):
                finished_at = event.get("time")
            continue
        step = steps.setdefault(step_id, {"id": step_id})
        if event.get("status") == "running" and "started_at" not in step:
            step["started_at"] = event.get("time")
        if event.get("status") in ("succeeded", "failed", "stopped", "skipped"):
            step["finished_at"] = event.get("time")
        if "iteration" in event:
            step["iterations"] = max(step.get("iterations", 0), event["iteration"])
        for key in (
            "status", "duration_ms", "file", "line", "source_target",
            "error_type", "diagnostic_error_type", "traceback",
            "blocked_by", "metrics", "reasons", "output_preview", "iteration",
        ):
            if key in event:
                step[key] = event[key]
        if "live_metrics" in event and isinstance(event["live_metrics"], dict):
            live = step.setdefault("live_metrics", {})
            live.update(event["live_metrics"])
        if "setting_change" in event:
            changes = step.setdefault("setting_changes", [])
            changes.append(event["setting_change"])
    return {
        "run_id": attempt.run_id,
        "status": attempt.status,
        "retry_count": attempt.retry_count,
        "crash_exhausted": attempt.crash_exhausted,
        "start_error": attempt.start_error,
        "started_at": started_at,
        "finished_at": finished_at,
        "steps": list(steps.values()),
        "result": result,
        "error": error,
    }


def register_execution_tools(server, access: WorkspaceAccess) -> None:
    from mcp.server.mcpserver.exceptions import ToolError
    from pydantic import Field

    from .tool_contract import RunId, SheetName, WorkspaceId, read_tool, write_tool

    @server.tool(**read_tool(
        "Read node settings",
        "Return each node's declared settings with their types, defaults, and saved values for a sheet. "
        "Use update_node_setting to change one.",
    ))
    async def read_node_settings(workspace_id: WorkspaceId, sheet: SheetName = "Main") -> dict[str, Any]:
        from anyio import to_thread

        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        settings = await to_thread.run_sync(
            _worker_json, flow_sheet, "GET", "/dashboard-settings/workflow", None,
        )
        return {"sheet": sheet, "settings": settings}

    @server.tool(**write_tool(
        "Update node setting",
        "Set one node setting. mode 'next' saves the value for future runs; mode 'live' sends it to the "
        "running flow without saving it.",
        destructive=True,
        idempotent=True,
    ))
    async def update_node_setting(
        workspace_id: WorkspaceId,
        node_id: Annotated[str, Field(description="Step ID that declares the setting.")],
        name: Annotated[str, Field(description="Setting name as listed by read_node_settings.")],
        value: Annotated[bool | int | float | str, Field(description="New value matching the setting's declared type.")],
        sheet: SheetName = "Main",
        mode: Annotated[Literal["next", "live"], Field(
            description="'next' saves for the next run; 'live' updates the active run.",
        )] = "next",
        run_id: Annotated[str | None, Field(
            description="Active run to update in 'live' mode. Omit to use the sheet's running run.",
        )] = None,
    ) -> dict[str, Any]:
        from anyio import to_thread

        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        target_run_id = run_id
        if mode == "live" and target_run_id is None:
            attempt = flow_sheet.attempt
            target_run_id = attempt.run_id if attempt is not None and attempt.status == "running" else None
        payload = {
            "nodeId": node_id,
            "name": name,
            "mode": mode,
            "value": value,
            "runId": target_run_id,
        }
        result = await to_thread.run_sync(
            _worker_json, flow_sheet, "POST", "/dashboard-settings/workflow", payload,
        )
        return {"sheet": sheet, "node_id": node_id, **result}

    @server.tool(**write_tool(
        "Run flow",
        "Start a run of the sheet's latest saved flow and return its run_id without waiting for it to finish. "
        "Flow code runs with the host account's access. Use inspect_execution to follow steps and results.",
        destructive=False,
        open_world=True,
    ))
    def run_flow(
        workspace_id: WorkspaceId,
        sheet: SheetName = "Main",
        input: Annotated[dict | None, Field(
            description="JSON object passed to the flow's inputs. Omit for an empty input.",
        )] = None,
    ) -> dict[str, Any]:
        from ..workspace_ui import ReloadConflict

        if input is not None and not isinstance(input, dict):
            raise ToolError("Flow input must be a JSON object.")
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            attempt = flow_sheet.start_run(input)
        except (FlowBusy, ReloadConflict, OSError, RuntimeError, ValueError) as error:
            raise ToolError(str(error)) from None
        return {"run_id": attempt.run_id, "sheet": sheet, "status": attempt.status}

    @server.tool(**write_tool(
        "Run all flows",
        "Start every idle sheet in the Workspace concurrently with an empty input. Sheets already running keep "
        "their current run. Returns the run ID of each started sheet and any per-sheet start errors.",
        destructive=False,
        open_world=True,
    ))
    async def run_all_flows(workspace_id: WorkspaceId) -> dict[str, Any]:
        from anyio import to_thread

        root = access.workspace(workspace_id)
        ensure_not_protected(root, access.protected_folders)
        try:
            resolver = access.sheet_resolver(workspace_id)
            result = await to_thread.run_sync(resolver.run_all_sheets)
        except (OSError, RuntimeError, ValueError) as error:
            raise ToolError(str(error) or "Could not run Workspace sheets.") from None
        return {"workspace_id": workspace_id, **result}

    @server.tool(**write_tool(
        "Stop flow",
        "Stop the sheet's active run and the work running in its process. Returns the sheet status afterward.",
        destructive=True,
        idempotent=True,
    ))
    def stop_flow(workspace_id: WorkspaceId, sheet: SheetName = "Main") -> dict[str, Any]:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            flow_sheet.stop()
        except (OSError, RuntimeError) as error:
            raise ToolError(str(error)) from None
        return {"sheet": sheet, "status": flow_sheet.status()}

    @server.tool(**read_tool(
        "Inspect execution",
        "Return a run's status, per-step timing, metrics, errors with source locations, and result, plus the "
        "20 most recent runs. Defaults to the active or latest run. Use read_console_logs for stdout and stderr.",
    ))
    def inspect_execution(
        workspace_id: WorkspaceId, sheet: SheetName = "Main", run_id: RunId = None,
    ) -> dict[str, Any]:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        history = RunHistory(flow_sheet.root)
        try:
            recent_runs = history.list_runs()[:20]
        except (OSError, ValueError) as error:
            raise ToolError(str(error) or "Could not read run history.") from None
        attempt = flow_sheet.attempt
        if run_id is not None:
            if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
                raise ToolError("Invalid run ID.")
            try:
                record = history.read(run_id)
            except (OSError, ValueError) as error:
                raise ToolError(str(error) or "Could not read run history.") from None
            active_run = _summarize_attempt(attempt, flow_sheet) if attempt is not None and attempt.run_id == run_id else None
            if record is None and active_run is None:
                raise ToolError(f"Unknown run {run_id!r}. Call inspect_execution without run_id to list recent runs.")
            return {
                "sheet": sheet,
                "status": flow_sheet.status(),
                "run_id": run_id,
                "active_run": active_run,
                "history": record,
                "recent_runs": recent_runs,
            }
        target_run_id = attempt.run_id if attempt is not None else (recent_runs[0]["id"] if recent_runs else None)
        try:
            record = history.read(target_run_id) if target_run_id is not None else None
        except (OSError, ValueError) as error:
            raise ToolError(str(error) or "Could not read run history.") from None
        active_run = _summarize_attempt(attempt, flow_sheet) if attempt is not None else None
        return {
            "sheet": sheet,
            "status": flow_sheet.status(),
            "run_id": target_run_id,
            "active_run": active_run,
            "history": record,
            "recent_runs": recent_runs,
        }

    @server.tool(**read_tool(
        "Read console logs",
        "Return a run's stdout and stderr, from live output or saved logs, with the sheet's log settings. "
        "Defaults to the active or latest run. Use inspect_execution for step status and results.",
    ))
    def read_console_logs(
        workspace_id: WorkspaceId, sheet: SheetName = "Main", run_id: RunId = None,
    ) -> dict[str, Any]:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        store = ConsoleLogStore(flow_sheet.root)
        try:
            current_settings = store.settings()
            attempt = flow_sheet.attempt
            live_events = []
            if attempt is not None and (run_id is None or attempt.run_id == run_id):
                live_events = [
                    event for event in attempt.snapshot_events()
                    if event.get("kind") == "console"
                ]
            target_run_id = run_id
            if target_run_id is None and attempt is not None:
                saved_events = store.read_run(attempt.run_id)
                if saved_events or live_events or attempt.status == "running" or not current_settings["save_logs"]:
                    target_run_id = attempt.run_id
            else:
                saved_events = store.read_run(target_run_id) if target_run_id is not None else []

            if target_run_id is not None:
                resolved = target_run_id
                if not saved_events or (not current_settings["save_logs"] and len(live_events) > len(saved_events)):
                    events = live_events
                else:
                    events = saved_events
            else:
                events = store.read_run(None)
                resolved = events[0]["run_id"] if events else None
        except (ValueError, OSError) as error:
            raise ToolError(str(error) or "Could not read console logs.") from None
        stdout = "".join(event["text"] for event in events if event.get("stream") == "stdout")
        stderr = "".join(event["text"] for event in events if event.get("stream") == "stderr")
        return {
            "sheet": sheet,
            "settings": current_settings,
            "run_id": resolved,
            "events": events,
            "stdout": stdout,
            "stderr": stderr,
        }

    @server.tool(**write_tool(
        "Configure console logs",
        "Change whether a sheet saves console logs and how many days they are kept. Omitted fields keep their "
        "current value. Shorter retention removes older saved logs.",
        destructive=True,
        idempotent=True,
    ))
    def configure_console_logs(
        workspace_id: WorkspaceId,
        sheet: SheetName = "Main",
        save_logs: Annotated[bool | None, Field(description="Whether to save console output for new runs.")] = None,
        retention_days: Annotated[int | None, Field(description="Days to keep saved logs.")] = None,
    ) -> dict[str, Any]:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        changes: dict[str, bool | int] = {}
        if save_logs is not None:
            changes["save_logs"] = save_logs
        if retention_days is not None:
            changes["retention_days"] = retention_days
        try:
            updated = ConsoleLogStore(flow_sheet.root).update_settings(changes)
        except (ValueError, OSError) as error:
            raise ToolError(str(error) or "Could not save console log settings.") from None
        return {"sheet": sheet, "settings": updated}
