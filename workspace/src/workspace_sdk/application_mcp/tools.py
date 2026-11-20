from .access import WorkspaceAccess
from .execution_tools import register_execution_tools
from .file_tools import register_file_tools
from .source_tools import register_source_tools
from .validation_tools import register_validation_tools
from .workspace_tools import register_dependencies, register_sheet_tools


def register_workspace_tools(server, access: WorkspaceAccess, transfers) -> None:
    register_sheet_tools(server, access)
    register_source_tools(server, access)
    register_file_tools(server, access, transfers)
    register_validation_tools(server, access)
    register_execution_tools(server, access)
    register_dependencies(server, access)
