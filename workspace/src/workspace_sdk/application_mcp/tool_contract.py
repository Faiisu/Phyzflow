from typing import Annotated

from mcp_types import ToolAnnotations
from pydantic import Field


def read_tool(title: str, description: str, *, open_world: bool = False) -> dict:
    return {
        "title": title,
        "description": description,
        "annotations": ToolAnnotations(
            title=title,
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=open_world,
        ),
        "structured_output": True,
    }


def write_tool(
    title: str,
    description: str,
    *,
    destructive: bool,
    idempotent: bool = False,
    open_world: bool = False,
) -> dict:
    return {
        "title": title,
        "description": description,
        "annotations": ToolAnnotations(
            title=title,
            read_only_hint=False,
            destructive_hint=destructive,
            idempotent_hint=idempotent,
            open_world_hint=open_world,
        ),
        "structured_output": True,
    }


WorkspaceId = Annotated[str, Field(description="Workspace ID returned by list_workspaces.")]
SheetName = Annotated[str, Field(
    description="Sheet name as listed by inspect_workspace. 'Main' addresses the root sheet.",
)]
RequiredEditSession = Annotated[str, Field(description="Edit session ID returned by open_sheet for this sheet.")]
OptionalEditSession = Annotated[str | None, Field(
    description="Edit session ID returned by open_sheet. Required only while this client holds the sheet's edit lock.",
)]
SheetPath = Annotated[str, Field(
    description="Forward-slash path relative to the sheet directory. From Main, flows/<Sheet>/... reads a secondary sheet.",
)]
SheetRevision = Annotated[str, Field(
    description="Revision returned by read_file, or 'missing' to create a file that does not exist yet.",
)]
WorkspacePath = Annotated[str, Field(description="Forward-slash path relative to the Workspace root.")]
WorkspaceDirectory = Annotated[str, Field(
    description="Forward-slash directory path relative to the Workspace root. '.' is the root.",
)]
FilesPath = Annotated[str, Field(
    description="Forward-slash path relative to the Workspace Files area (.workspace/files). '.' is its root.",
)]
FileRevision = Annotated[str, Field(
    description="Current revision from stat_workspace_file, list_workspace_files, or read_workspace_text, "
    "or 'missing' when the path must not exist yet.",
)]
PageCursor = Annotated[str | None, Field(description="next_after from the previous page. Omit for the first page.")]
SessionIds = Annotated[dict[str, str] | None, Field(
    description="Edit session IDs keyed by each affected sheet's Workspace-relative directory, "
    "such as '.' for Main or 'flows/Secondary'. Needed only for sheets with an active edit lock.",
)]
RunId = Annotated[str | None, Field(
    description="32-character run ID from run_flow or inspect_execution. Omit for the active or most recent run.",
)]
TransferId = Annotated[str, Field(description="Transfer ID returned by a begin_* transfer tool.")]
ByteOffset = Annotated[int, Field(description="Byte offset of this chunk. It equals next_offset from the previous chunk.")]
ByteSize = Annotated[int, Field(description="Exact total size of the file in bytes.")]
Sha256Digest = Annotated[str, Field(description="Lowercase hexadecimal SHA-256 digest of the complete file.")]
