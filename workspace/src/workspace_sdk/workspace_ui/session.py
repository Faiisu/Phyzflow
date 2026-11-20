from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from time import monotonic, sleep

from ..execution import FlowBusy, sheet_busy
from ..history import RunHistory
from ..project import workspace_format_notice
from ..sheet_locks import SheetLockedError
from ..sheet_runs import RunStateStore, SheetRun
from ..sheets import (
    archive_sheet_directory,
    can_delete_sheet,
    create_sheet,
    discover_flows,
)
from ..ui_settings import UISettings
from .flow_sheet import FlowSheet
from .processes import WorkspaceProcessRunningError, verify_workspace_processes_stopped
from .routes import workspace_dispatch


def workspace_routes(
    root: Path,
    flows: list[tuple[str, Path]],
    settings: UISettings,
    *,
    interpreter: Path | None = None,
    workspace_id: str | None = None,
    run_state: RunStateStore | None = None,
    app_closing: Callable[[], bool] = lambda: False,
):
    def make_sheet(title: str, path: Path) -> FlowSheet:
        sheet_key = "." if path == root else path.relative_to(root).as_posix()
        return FlowSheet(
            title, path, settings.zoom, workspace_root=root, main=path == root, interpreter=interpreter,
            workspace_closing=lambda: closing or app_closing(),
            workspace_id=workspace_id, sheet_key=sheet_key, run_state=run_state,
        )

    closing = False
    sheets: list[FlowSheet | None] = [make_sheet(title, path) for title, path in flows]
    sheets_lock = RLock()

    def restore_states() -> None:
        if run_state is None or workspace_id is None:
            return
        states = run_state.workspace_states(workspace_id)
        if not states:
            return
        to_start: list[tuple[FlowSheet, dict | None, int]] = []
        for sheet in list(sheets):
            if sheet is None or sheet.sheet_key is None or sheet.sheet_key not in states:
                continue
            saved = states[sheet.sheet_key]
            sheet.run_generation = max(sheet.run_generation, saved["generation"])
            status = saved["status"]
            if status == "running":
                try:
                    record = RunHistory(sheet.root).read(saved["run_id"])
                except (OSError, ValueError):
                    record = None
                if record is not None and record.get("status") in ("succeeded", "failed"):
                    status = record["status"]
                    run_state.record_terminal(
                        workspace_id, sheet.sheet_key, saved["generation"], saved["run_id"], status,
                    )
            if status == "running":
                to_start.append((sheet, saved["replay_input"], saved["generation"]))
            elif status == "stopped":
                sheet.inactive_status = "stopped"
            elif status in ("succeeded", "failed"):
                sheet.attempt = SheetRun(
                    None,
                    generation=saved["generation"],
                    run_id=saved["run_id"],
                    status=status,
                )
                sheet.inactive_status = "ready"

        def start_restored(sheet: FlowSheet, replay_input: dict | None, generation: int) -> None:
            deadline = monotonic() + 5
            while not closing and not app_closing() and sheet_busy(sheet.root) and monotonic() < deadline:
                sleep(.05)
            if closing or app_closing():
                return
            try:
                sheet.start_run(replay_input)
            except (FlowBusy, OSError, RuntimeError, ValueError) as failure:
                with sheet.lock:
                    if sheet.attempt is None and not (closing or app_closing()):
                        sheet.run_generation = max(sheet.run_generation, generation) + 1
                        sheet.attempt = SheetRun(
                            None,
                            generation=sheet.run_generation,
                            status="failed",
                            start_error=str(failure),
                        )
                        run_state.record_terminal(
                            workspace_id,
                            sheet.sheet_key,
                            sheet.attempt.generation,
                            sheet.attempt.run_id,
                            "failed",
                            str(failure),
                        )

        if to_start:
            with ThreadPoolExecutor(max_workers=len(to_start)) as pool:
                futures = [
                    pool.submit(start_restored, sheet, replay_input, generation)
                    for sheet, replay_input, generation in to_start
                ]
                for future in futures:
                    future.result()

    restore_states()

    def resolve_sheet(sheet_path: Path) -> FlowSheet:
        target = sheet_path.resolve()
        with sheets_lock:
            if closing:
                raise RuntimeError("Workspace is shutting down.")
            for existing in sheets:
                if existing is not None and existing.root.resolve() == target:
                    return existing
            known = {sheet.root.resolve() for sheet in sheets if sheet is not None}
            for title, path in discover_flows(root):
                if path.resolve() not in known:
                    sheets.append(make_sheet(title, path))
            for existing in sheets:
                if existing is not None and existing.root.resolve() == target:
                    return existing
        raise FileNotFoundError(f"Unknown flow sheet: {sheet_path}")

    def discovered_sheets() -> list[FlowSheet | None]:
        with sheets_lock:
            if not closing:
                known = {sheet.root for sheet in sheets if sheet is not None}
                sheets.extend(
                    make_sheet(title, path)
                    for title, path in discover_flows(root)
                    if path not in known
                )
            return list(sheets)

    def create_workspace_sheet(name: object) -> dict:
        with sheets_lock:
            if closing:
                raise RuntimeError("Workspace is shutting down.")
            workspace_format_notice(root)
            directory = create_sheet(root, name).parent
            index = len(sheets)
            sheets.append(make_sheet(directory.name, directory))
        return {
            "index": index,
            "name": directory.name,
            "entrypoint": (directory / "flow.py").relative_to(root).as_posix(),
        }

    def delete_workspace_sheet(
        sheet_root: Path,
        *,
        principal: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        target = sheet_root.resolve()
        with sheets_lock:
            if closing:
                raise RuntimeError("This sheet is no longer available.")
            index = next(
                (
                    i for i, existing in enumerate(sheets)
                    if existing is not None and existing.root.resolve() == target
                ),
                None,
            )
            if index is None:
                raise FileNotFoundError("This sheet is no longer available.")
            sheet = sheets[index]
            if not can_delete_sheet(root, sheet.root):
                raise ValueError("Only sheets stored directly in this Workspace can be deleted.")
            if principal is not None:
                sheet.edit_lease.verify_write(principal, session_id)
            else:
                lock_status = sheet.edit_lease.status()
                if lock_status["locked"]:
                    raise SheetLockedError(
                        f"This sheet is locked for editing by {lock_status['owner']}.",
                        snapshot=lock_status,
                    )
            archive_sheet_directory(root, sheet.root, sheet.archive)
            # Keep existing flow URLs tied to their original sheets.
            sheets[index] = None
        return {"status": "deleted"}

    def run_all_sheets(*, by_index: bool = False) -> dict:
        current = {index: sheet for index, sheet in enumerate(discovered_sheets()) if sheet is not None}
        started, errors = {}, {}
        with ThreadPoolExecutor() as pool:
            pending = {index: pool.submit(sheet.start_run, None) for index, sheet in current.items()}
            for index, admission in pending.items():
                key = index if by_index else current[index].title
                try:
                    started[key] = admission.result().run_id
                except FlowBusy:
                    pass
                except (OSError, RuntimeError, ValueError) as error:
                    errors[key] = str(error)
        return {"started": started, "errors": errors}

    def file_leases():
        with sheets_lock:
            if closing:
                raise RuntimeError("Workspace is shutting down.")
            return {sheet.root: sheet.edit_lease for sheet in sheets if sheet is not None}

    resolve_sheet.file_leases = file_leases
    resolve_sheet.create_workspace_sheet = create_workspace_sheet
    resolve_sheet.delete_workspace_sheet = delete_workspace_sheet
    resolve_sheet.run_all_sheets = run_all_sheets
    resolve_sheet.file_guard = sheets_lock

    dispatch = workspace_dispatch(
        root, sheets, sheets_lock,
        interpreter=interpreter,
        is_closing=lambda: closing,
        discovered_sheets=discovered_sheets,
        create_workspace_sheet=create_workspace_sheet,
        delete_workspace_sheet=delete_workspace_sheet,
        run_all_sheets=run_all_sheets,
    )

    def close(*, delete: bool = False) -> None:
        nonlocal closing
        with sheets_lock:
            closing = True
            current = [sheet for sheet in sheets if sheet is not None]
        if not delete:
            with ThreadPoolExecutor() as pool:
                pending = [pool.submit(sheet.stop, shutdown=True) for sheet in current]
                for stop in pending:
                    stop.result()
            return
        stop_errors: list[str] = []
        with ThreadPoolExecutor() as pool:
            for sheet in current:
                sheet.edit_lease.close()
            pending = [pool.submit(sheet.stop) for sheet in current]
            for stop in pending:
                try:
                    stop.result()
                except OSError as error:
                    stop_errors.append(str(error))
        tracked = tuple(
            sheet.worker.process for sheet in current if sheet.worker is not None
        )
        try:
            verify_workspace_processes_stopped(
                root,
                tracked,
                wait_seconds=0.0 if stop_errors else 0.5,
            )
            if stop_errors:
                raise WorkspaceProcessRunningError(
                    f"Could not delete {root.name} because a Workspace process is still running. "
                    "End the process or restart the host through the operating system before retrying Delete."
                )
        except WorkspaceProcessRunningError:
            with sheets_lock:
                closing = False
            raise

    return dispatch, close, resolve_sheet
