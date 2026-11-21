from typing import Literal

from ..dashboard import DASHBOARD_THEME_REFERENCE, inspect_sheet_dashboards, inspect_workspace_dashboards
from ..sheet_locks import SheetLockedError
from ..sheets import discover_flows
from ..workspace_environment import install_workspace_packages
from .access import WorkspaceAccess, ensure_not_protected, resolve_workspace_sheet


def register_sheet_tools(server, access: WorkspaceAccess) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    @server.tool(description="List the Workspaces granted to this MCP identity.")
    def list_workspaces() -> dict:
        return {"workspaces": access.list()}

    @server.tool(description="Inspect the sheets in a granted Workspace without loading its flows.")
    def inspect_workspace(workspace_id: str) -> dict:
        root = access.workspace(workspace_id)
        ensure_not_protected(root, access.protected_folders)
        sheets = [
            {"name": name, "entrypoint": (sheet / "flow.py").relative_to(root).as_posix()}
            for name, sheet in discover_flows(root)
        ]
        return {"id": workspace_id, "name": root.name, "sheets": sheets}

    @server.tool(description="Create a new flow sheet in a granted Workspace.")
    def create_sheet(workspace_id: str, name: str) -> dict:
        root = access.workspace(workspace_id)
        ensure_not_protected(root, access.protected_folders)
        try:
            resolver = access.sheet_resolver(workspace_id)
            created = resolver.create_workspace_sheet(name)
        except (FileExistsError, OSError, RuntimeError, ValueError) as error:
            raise ToolError(str(error) or "Could not create sheet.") from None
        return {"workspace_id": workspace_id, **created}

    @server.tool(description="Delete a secondary flow sheet in a granted Workspace, moving it to .workspace/deleted-flows.")
    def delete_sheet(workspace_id: str, sheet: str, session_id: str | None = None) -> dict:
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

    @server.tool(description="Rename a sheet without moving its project files or interrupting its runs.")
    def rename_sheet(workspace_id: str, sheet: str, name: str, session_id: str | None = None) -> dict:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            resolver = access.sheet_resolver(workspace_id)
            return resolver.rename_workspace_sheet(flow_sheet, name, principal=access.principal(), session_id=session_id)
        except (SheetLockedError, OSError, RuntimeError, ValueError) as error:
            raise ToolError(str(error) or f"Could not rename sheet {sheet!r}.") from None

    @server.tool(description=(
        "Inspect dashboard panels, asset paths, theme contract, and resize guidance through MCP. "
        "Pass sheet: null for the Workspace Dashboard or a sheet name for its Workflow and Node panels. "
        "Use the MCP file tools to edit the returned assets. A browser URL is not required."
    ))
    def inspect_dashboards(workspace_id: str, sheet: str | None = "Main") -> dict:
        if sheet is None:
            root = access.workspace(workspace_id)
            ensure_not_protected(root, access.protected_folders)
            result = {"theme_reference": DASHBOARD_THEME_REFERENCE}
        else:
            root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
            result = {"sheet": sheet, **inspect_sheet_dashboards(flow_sheet.root)}
        return {**result, "workspace": inspect_workspace_dashboards(root)}

    @server.tool(description="Open a sheet for exclusive editing or read-only inspection.")
    def open_sheet(workspace_id: str, sheet: str = "Main", mode: Literal["edit", "read"] = "edit") -> dict:
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        entrypoint = (flow_sheet.root / "flow.py").relative_to(root).as_posix()
        if mode == "read":
            lock_status = flow_sheet.edit_lease.status()
            attempt = flow_sheet.attempt
            return {
                "sheet": sheet,
                "mode": "read",
                "entrypoint": entrypoint,
                "status": flow_sheet.status(),
                "run_id": attempt.run_id if attempt is not None else None,
                "read_only": lock_status["locked"],
                "edit_lock": lock_status,
            }
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

    @server.tool(description="Refresh the heartbeat for an active sheet editing session.")
    def heartbeat_sheet(workspace_id: str, sheet: str = "Main", session_id: str = "") -> dict:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            return flow_sheet.edit_lease.heartbeat(access.principal(), session_id)
        except (SheetLockedError, ValueError, OSError) as error:
            raise ToolError(str(error)) from None

    @server.tool(description="Close an active sheet editing session, clean up its processes, and release the lock.")
    def close_sheet(
        workspace_id: str,
        sheet: str = "Main",
        session_id: str = "",
        outcome: Literal["succeeded", "failed", "cancelled"] = "succeeded",
    ) -> dict:
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        try:
            return flow_sheet.edit_lease.release(access.principal(), session_id, outcome=outcome)
        except (SheetLockedError, ValueError, OSError) as error:
            raise ToolError(str(error)) from None



def register_dependencies(server, access: WorkspaceAccess) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    @server.tool(description="Install Python package dependencies into a granted Workspace's shared .venv interpreter.")
    async def install_dependencies(workspace_id: str, packages: list[str]) -> dict:
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
