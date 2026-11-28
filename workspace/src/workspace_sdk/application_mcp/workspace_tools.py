import sqlite3
from typing import Annotated, Any, Literal

from ..dashboard import DASHBOARD_THEME_REFERENCE, inspect_sheet_dashboards
from ..sheet_locks import SheetLockedError
from ..sheets import discover_flows
from ..workspace_environment import install_workspace_packages
from .access import WorkspaceAccess, ensure_not_protected, resolve_workspace_sheet


def register_sheet_tools(server, access: WorkspaceAccess) -> None:
    from mcp.server.mcpserver.exceptions import ToolError
    from pydantic import Field

    from .tool_contract import (
        OptionalEditSession,
        RequiredEditSession,
        SheetName,
        WorkspaceId,
        read_tool,
        write_tool,
    )

    @server.tool(**read_tool(
        "List Workspaces",
        "List the Workspaces this MCP token may access, with their IDs and names. "
        "Use inspect_workspace to list the sheets inside one.",
    ))
    def list_workspaces() -> dict[str, Any]:
        return {"workspaces": access.list()}

    @server.tool(**read_tool(
        "Inspect Workspace",
        "List a granted Workspace's sheets and their flow.py entrypoints without loading any flow. "
        "Use inspect_sheet for one sheet's run status and edit lock.",
    ))
    def inspect_workspace(workspace_id: WorkspaceId) -> dict[str, Any]:
        root = access.workspace(workspace_id)
        ensure_not_protected(root, access.protected_folders)
        sheets = [
            {"name": name, "entrypoint": (sheet / "flow.py").relative_to(root).as_posix()}
            for name, sheet in discover_flows(root)
        ]
        return {"id": workspace_id, "name": root.name, "sheets": sheets}

    @server.tool(**write_tool(
        "Create sheet",
        "Create a secondary sheet with a starter flow.py. Fails if the name is already taken.",
        destructive=False,
    ))
    def create_sheet(
        workspace_id: WorkspaceId,
        name: Annotated[str, Field(description="New sheet name, unique within the Workspace.")],
    ) -> dict[str, Any]:
        root = access.workspace(workspace_id)
        ensure_not_protected(root, access.protected_folders)
        try:
            resolver = access.sheet_resolver(workspace_id)
            created = resolver.create_workspace_sheet(name)
        except (FileExistsError, OSError, RuntimeError, ValueError) as error:
            raise ToolError(str(error) or "Could not create sheet.") from None
        return {"workspace_id": workspace_id, **created}

    @server.tool(**write_tool(
        "Delete sheet",
        "Delete a secondary sheet by moving its directory to .workspace/deleted-flows. The Main sheet cannot be deleted.",
        destructive=True,
    ))
    def delete_sheet(
        workspace_id: WorkspaceId, sheet: SheetName, session_id: OptionalEditSession = None,
    ) -> dict[str, Any]:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            resolver = access.sheet_resolver(workspace_id)
            result = resolver.delete_workspace_sheet(
                flow_sheet.root,
                principal=access.principal(),
                session_id=session_id,
            )
        except (SheetLockedError, FileNotFoundError, OSError, RuntimeError, ValueError) as error:
            raise ToolError(str(error) or f"Could not delete sheet {sheet!r}.") from None
        return {"sheet": sheet, **result}

    @server.tool(**write_tool(
        "Rename sheet",
        "Change a sheet's display name. Its directory, active runs, history, and dashboard placements stay the same.",
        destructive=False,
    ))
    def rename_sheet(
        workspace_id: WorkspaceId,
        sheet: SheetName,
        name: Annotated[str, Field(description="New sheet name, unique within the Workspace.")],
        session_id: OptionalEditSession = None,
    ) -> dict[str, Any]:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            resolver = access.sheet_resolver(workspace_id)
            return resolver.rename_workspace_sheet(flow_sheet, name, principal=access.principal(), session_id=session_id)
        except (SheetLockedError, OSError, RuntimeError, ValueError) as error:
            raise ToolError(str(error) or f"Could not rename sheet {sheet!r}.") from None

    @server.tool(**read_tool(
        "Inspect dashboards",
        "Return dashboard panels, their asset paths, the theme contract, and Workspace Dashboard placements. "
        "With sheet set to null, returns the Workspace Dashboard catalog and placements; with a sheet name, "
        "also returns that sheet's Workflow and Node panels. Panel assets are edited with write_file or "
        "apply_patch in their source sheet. Files under a Workflow panel's bridge.json file_root are read "
        "with read_workspace_text or begin_workspace_file_download.",
    ))
    def inspect_dashboards(
        workspace_id: WorkspaceId,
        sheet: Annotated[str | None, Field(
            description="Sheet name whose panels to include, or null for only the Workspace Dashboard.",
        )] = "Main",
    ) -> dict[str, Any]:
        if sheet is None:
            root = access.workspace(workspace_id)
            ensure_not_protected(root, access.protected_folders)
            result = {"theme_reference": DASHBOARD_THEME_REFERENCE}
        else:
            root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
            result = {
                "sheet": sheet,
                "sheet_path": flow_sheet.root.relative_to(root).as_posix(),
                **inspect_sheet_dashboards(flow_sheet.root),
            }
        try:
            resolver = access.sheet_resolver(workspace_id)
            workspace_info = resolver.inspect_workspace_dashboard()
        except (FileNotFoundError, OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            raise ToolError(str(error) or "Could not inspect Workspace dashboards.") from None
        return {**result, "workspace": workspace_info}

    @server.tool(**write_tool(
        "Add dashboard placement",
        "Place an existing Workflow or Node panel from a sheet on the Workspace Dashboard. "
        "Returns the updated Workspace Dashboard with the new placement ID.",
        destructive=False,
    ))
    def add_dashboard_placement(
        workspace_id: WorkspaceId,
        sheet: Annotated[str, Field(
            description="Source sheet name or its Workspace-relative directory.",
        )] = "Main",
        scope: Annotated[str | None, Field(
            description="Null for a Workflow panel, or the step ID that owns a Node panel.",
        )] = None,
        panel_id: Annotated[str, Field(description="Panel ID as listed by inspect_dashboards.")] = "default",
    ) -> dict[str, Any]:
        root = access.workspace(workspace_id)
        ensure_not_protected(root, access.protected_folders)
        try:
            resolver = access.sheet_resolver(workspace_id)
            workspace_info = resolver.add_workspace_dashboard_placement(
                sheet, scope, panel_id, allow_sheet_name=True,
            )
        except (FileNotFoundError, OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            raise ToolError(str(error) or "Could not add dashboard placement.") from None
        return {"workspace_id": workspace_id, **workspace_info}

    @server.tool(**write_tool(
        "Remove dashboard placement",
        "Remove a placement from the Workspace Dashboard. The source panel and its assets stay in their sheet.",
        destructive=True,
    ))
    def remove_dashboard_placement(
        workspace_id: WorkspaceId,
        placement_id: Annotated[str, Field(description="Placement ID as listed by inspect_dashboards.")],
    ) -> dict[str, Any]:
        root = access.workspace(workspace_id)
        ensure_not_protected(root, access.protected_folders)
        try:
            resolver = access.sheet_resolver(workspace_id)
            workspace_info = resolver.remove_workspace_dashboard_placement(placement_id)
        except (FileNotFoundError, OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            raise ToolError(str(error) or "Could not remove dashboard placement.") from None
        return {"workspace_id": workspace_id, **workspace_info}

    @server.tool(**read_tool(
        "Inspect sheet",
        "Return a sheet's entrypoint, run status, active run ID, and edit lock without acquiring the lock. "
        "read_only is true while another editor holds the lock. Use open_sheet to start editing.",
    ))
    def inspect_sheet(workspace_id: WorkspaceId, sheet: SheetName = "Main") -> dict[str, Any]:
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        lock_status = flow_sheet.edit_lease.status()
        attempt = flow_sheet.attempt
        return {
            "sheet": sheet,
            "mode": "read",
            "entrypoint": (flow_sheet.root / "flow.py").relative_to(root).as_posix(),
            "status": flow_sheet.status(),
            "run_id": attempt.run_id if attempt is not None else None,
            "read_only": lock_status["locked"],
            "edit_lock": lock_status,
        }

    @server.tool(**write_tool(
        "Open sheet for editing",
        "Acquire the sheet's exclusive edit lock and return a session_id for edits, heartbeats, and close_sheet. "
        "Fails while another editor holds the lock. Use inspect_sheet to read lock state without acquiring it.",
        destructive=False,
    ))
    def open_sheet(workspace_id: WorkspaceId, sheet: SheetName = "Main") -> dict[str, Any]:
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        entrypoint = (flow_sheet.root / "flow.py").relative_to(root).as_posix()
        try:
            lock_status = flow_sheet.edit_lease.acquire(
                principal=access.principal(),
                principal_type="mcp",
                owner=access.owner_label(),
            )
        except (SheetLockedError, ValueError, OSError) as error:
            raise ToolError(str(error)) from None
        return {
            "session_id": lock_status["session_id"],
            "sheet": sheet,
            "mode": "edit",
            "entrypoint": entrypoint,
            "edit_lock": lock_status,
        }

    @server.tool(**write_tool(
        "Refresh edit session",
        "Extend an active editing session so its lock is not treated as stale. Returns the current lock state.",
        destructive=False,
        idempotent=True,
    ))
    def heartbeat_sheet(
        workspace_id: WorkspaceId, sheet: SheetName = "Main", session_id: RequiredEditSession = "",
    ) -> dict[str, Any]:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            return flow_sheet.edit_lease.heartbeat(access.principal(), session_id)
        except (SheetLockedError, ValueError, OSError) as error:
            raise ToolError(str(error)) from None

    @server.tool(**write_tool(
        "Close edit session",
        "End an editing session: stop the processes it started and release the lock. Saved files are kept.",
        destructive=False,
    ))
    def close_sheet(
        workspace_id: WorkspaceId,
        sheet: SheetName = "Main",
        session_id: RequiredEditSession = "",
        outcome: Annotated[Literal["succeeded", "failed", "cancelled"], Field(
            description="How the editing operation ended. Echoed in the result.",
        )] = "succeeded",
    ) -> dict[str, Any]:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            return flow_sheet.edit_lease.release(access.principal(), session_id, outcome=outcome)
        except (SheetLockedError, ValueError, OSError) as error:
            raise ToolError(str(error)) from None



def register_dependencies(server, access: WorkspaceAccess) -> None:
    from mcp.server.mcpserver.exceptions import ToolError
    from pydantic import Field

    from .tool_contract import WorkspaceId, write_tool

    @server.tool(**write_tool(
        "Install dependencies",
        "Install Python packages from the package index into the Workspace's shared .venv. "
        "Every sheet in the Workspace uses that environment. Existing versions may be upgraded or replaced.",
        destructive=True,
        idempotent=True,
        open_world=True,
    ))
    async def install_dependencies(
        workspace_id: WorkspaceId,
        packages: Annotated[list[str], Field(
            description="Package requirement specifiers, such as 'numpy' or 'pandas==2.2.3'.",
        )],
    ) -> dict[str, Any]:
        from anyio import to_thread

        root = access.workspace(workspace_id)
        ensure_not_protected(root, access.protected_folders)
        try:
            result = await to_thread.run_sync(
                lambda: install_workspace_packages(
                    root,
                    packages,
                    protected_folders=access.protected_folders,
                )
            )
        except (ValueError, RuntimeError, OSError) as error:
            raise ToolError(str(error) or "Could not install Workspace packages.") from None
        return {"workspace_id": workspace_id, **result}
