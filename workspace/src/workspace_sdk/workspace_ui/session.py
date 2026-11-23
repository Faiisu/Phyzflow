from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
from threading import RLock
from time import monotonic, sleep

from ..dashboard import PANEL_ID_PATTERN, dashboard_panel_label, dashboard_panels, sheet_panel_catalog
from ..execution import FlowBusy, sheet_busy
from ..history import RunHistory
from ..project import ValidationError, workspace_format_notice
from ..sheet_locks import SheetLockedError
from ..sheet_runs import RunStateStore, SheetRun
from ..sheets import (
    archive_sheet_directory,
    can_delete_sheet,
    create_sheet,
    discover_flows,
    find_sheet,
    find_sheet_by_path,
    set_sheet_name,
    sheet_name,
    validate_sheet_name,
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
    dashboard_state: RunStateStore | None = None,
    protected_folders: tuple[Path, ...] = (),
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
    format_cache: tuple[tuple[int, int, int] | None, str | None, str | None] | None = None
    name_cache: dict[Path, tuple[tuple[int, int, int] | None, str]] = {}

    def _file_stat_key(path: Path) -> tuple[int, int, int] | None:
        try:
            if path.is_symlink():
                return (-1, -1, -1)
            info = path.stat()
            return (info.st_ino, info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            return None
        except OSError:
            return (-2, -2, -2)

    def cached_format_notice() -> str | None:
        nonlocal format_cache
        key = _file_stat_key(root / "pyproject.toml")
        if format_cache is not None and key not in ((-1, -1, -1), (-2, -2, -2)) and format_cache[0] == key:
            _, cached_notice, cached_error = format_cache
            if cached_error is not None:
                raise ValidationError(cached_error)
            return cached_notice
        try:
            notice = workspace_format_notice(root)
        except ValidationError as error:
            format_cache = (key, None, str(error))
            raise
        format_cache = (key, notice, None)
        return notice

    def cached_sheet_name(path: Path, default: str) -> str:
        if path.is_symlink():
            return default
        key = _file_stat_key(path / ".workspace" / "sheet-name")
        cached = name_cache.get(path)
        if cached is not None and key not in ((-1, -1, -1), (-2, -2, -2)) and cached[0] == key:
            return cached[1]
        resolved = default if key is None else sheet_name(path, default)
        name_cache[path] = (key, resolved)
        return resolved

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
        with sheets_lock:
            if closing:
                raise RuntimeError("Workspace is shutting down.")
            if not sheet_path.is_relative_to(root) or find_sheet_by_path(root, sheet_path.relative_to(root)) is None:
                raise FileNotFoundError(f"Unknown flow sheet: {sheet_path}")
            for existing in sheets:
                if existing is not None and existing.root == sheet_path:
                    return existing
            known = {sheet.root for sheet in sheets if sheet is not None}
            for title, path in discover_flows(root):
                if path not in known:
                    sheets.append(make_sheet(title, path))
            for existing in sheets:
                if existing is not None and existing.root == sheet_path:
                    return existing
        raise FileNotFoundError(f"Unknown flow sheet: {sheet_path}")

    def discovered_sheets() -> list[FlowSheet | None]:
        with sheets_lock:
            if not closing:
                cached_format_notice()
                folder = root / "flows"
                found = []
                if folder.is_dir():
                    found = [
                        (cached_sheet_name(path, path.name), path)
                        for path in sorted(folder.iterdir())
                        if (path / "flow.py").is_file()
                    ]
                if (root / "flow.py").is_file():
                    found.insert(0, (cached_sheet_name(root, "Main"), root))
                known = {sheet.root: sheet for sheet in sheets if sheet is not None}
                for title, path in found:
                    if path in known:
                        known[path].title = title
                    else:
                        sheets.append(make_sheet(title, path))
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

    def rename_workspace_sheet(
        sheet: FlowSheet, value: object, *, principal: str, session_id: str | None = None,
    ) -> dict:
        name = validate_sheet_name(value)
        with sheets_lock:
            if closing or sheet.closing or sheet not in sheets or (
                find_sheet_by_path(root, sheet.root.relative_to(root)) is None
            ):
                raise FileNotFoundError("This sheet is no longer available.")
            if (sheet.root != root and name.casefold() == "main") or any(
                path != sheet.root and title.casefold() == name.casefold() for title, path in discover_flows(root)
            ):
                raise FileExistsError("That sheet name is already in use. Choose another name.")
            sheet.edit_lease.write_locked(principal, session_id, lambda: set_sheet_name(sheet.root, name))
            sheet.title = name
            return {"name": name, "entrypoint": (sheet.root / "flow.py").relative_to(root).as_posix()}

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

    active_dashboard_state = dashboard_state or run_state
    dashboard_id = workspace_id or "."

    def dashboard_sheets() -> dict[Path, tuple[int, FlowSheet]]:
        try:
            current_list = discovered_sheets()
        except ValidationError:
            with sheets_lock:
                current_list = list(sheets)
        return {
            sheet.root: (index, sheet)
            for index, sheet in enumerate(current_list)
            if sheet is not None and not sheet.closing
        }

    def panel_scope_available(sheet: FlowSheet, scope: str | None) -> bool:
        if scope is None:
            return True
        step_ids = sheet.dashboard_step_ids()
        return step_ids is None or scope in step_ids

    def inspect_workspace_dashboard() -> dict:
        if active_dashboard_state is None or closing:
            raise FileNotFoundError("This Workspace is unavailable.")
        current = dashboard_sheets()
        placements = active_dashboard_state.workspace_placements(dashboard_id)
        for placement in placements:
            path = find_sheet_by_path(root, placement["sheet"])
            index, sheet = current.get(path, (None, None))
            scope, panel_id = placement["scope"], placement["panel_id"]
            available = sheet is not None and panel_scope_available(sheet, scope) and any(
                panel["id"] == panel_id for panel in dashboard_panels(path, scope)
            )
            placement.update(
                sheet_name=sheet.title if sheet is not None else placement["sheet"],
                sheet_index=index,
                available=available,
                label=dashboard_panel_label(panel_id),
            )
        sources = discover_flows(root)
        node_ids = {path: current[path][1].dashboard_step_ids() for _, path in sources if path in current}
        catalog = sheet_panel_catalog(root, sources, node_ids)
        return {
            "placements": placements,
            "catalog": catalog,
        }

    def add_workspace_dashboard_placement(
        sheet_ref: object,
        scope: object = None,
        panel_id: object = "default",
        *,
        allow_sheet_name: bool = False,
    ) -> dict:
        if active_dashboard_state is None or closing:
            raise FileNotFoundError("This Workspace is unavailable.")
        if (
            not isinstance(sheet_ref, str)
            or not sheet_ref
            or (scope is not None and (not isinstance(scope, str) or not scope or "\x00" in scope))
            or not isinstance(panel_id, str)
            or not PANEL_ID_PATTERN.fullmatch(panel_id)
            or panel_id in (".", "..")
        ):
            raise ValueError("Send a valid dashboard placement.")
        with sheets_lock:
            source = find_sheet(root, sheet_ref) if allow_sheet_name else None
            if source is None:
                source = find_sheet_by_path(root, sheet_ref)
            entry = dashboard_sheets().get(source)
            if entry is None or not panel_scope_available(entry[1], scope) or not any(
                panel["id"] == panel_id for panel in dashboard_panels(source, scope)
            ):
                raise FileNotFoundError("Source panel unavailable in this Workspace.")
            active_dashboard_state.add_workspace_placement(
                dashboard_id,
                source.relative_to(root).as_posix(),
                scope,
                panel_id,
            )
        return inspect_workspace_dashboard()

    def remove_workspace_dashboard_placement(placement_id: object, *, allow_missing: bool = False) -> dict:
        if active_dashboard_state is None or closing:
            raise FileNotFoundError("This Workspace is unavailable.")
        if not isinstance(placement_id, str) or not re.fullmatch(r"[0-9a-f]{32}", placement_id):
            raise ValueError("Send a valid dashboard placement ID.")
        if not allow_missing and active_dashboard_state.find_workspace_placement(dashboard_id, placement_id) is None:
            raise FileNotFoundError("Dashboard placement not found in this Workspace.")
        active_dashboard_state.remove_workspace_placement(dashboard_id, placement_id)
        return inspect_workspace_dashboard()

    def file_leases():
        with sheets_lock:
            if closing:
                raise RuntimeError("Workspace is shutting down.")
            return {sheet.root: sheet.edit_lease for sheet in sheets if sheet is not None}

    resolve_sheet.file_leases = file_leases
    resolve_sheet.create_workspace_sheet = create_workspace_sheet
    resolve_sheet.rename_workspace_sheet = rename_workspace_sheet
    resolve_sheet.delete_workspace_sheet = delete_workspace_sheet
    resolve_sheet.run_all_sheets = run_all_sheets
    resolve_sheet.inspect_workspace_dashboard = inspect_workspace_dashboard
    resolve_sheet.add_workspace_dashboard_placement = add_workspace_dashboard_placement
    resolve_sheet.remove_workspace_dashboard_placement = remove_workspace_dashboard_placement
    resolve_sheet.file_guard = sheets_lock
    discovered_sheets.format_notice = cached_format_notice

    dispatch = workspace_dispatch(
        root, sheets, sheets_lock,
        interpreter=interpreter,
        is_closing=lambda: closing,
        discovered_sheets=discovered_sheets,
        create_workspace_sheet=create_workspace_sheet,
        rename_workspace_sheet=rename_workspace_sheet,
        delete_workspace_sheet=delete_workspace_sheet,
        run_all_sheets=run_all_sheets,
        workspace_id=workspace_id,
        run_state=run_state,
        dashboard_state=active_dashboard_state,
        protected_folders=protected_folders,
        inspect_workspace_dashboard=inspect_workspace_dashboard,
        add_workspace_dashboard_placement=add_workspace_dashboard_placement,
        remove_workspace_dashboard_placement=remove_workspace_dashboard_placement,
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
