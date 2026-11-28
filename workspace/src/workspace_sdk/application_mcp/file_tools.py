from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any

from ..node_sources import MAX_SOURCE_BYTES
from .access import ensure_not_protected
from .file_paths import (
    STAGING_PREFIX, atomic_move, checked_path, file_errors, file_metadata, is_entrypoint,
    mutate_files, open_file, relative_path, require_file, revision as file_revision, verify_revision,
)
from .file_transfers import CHUNK_BYTES, FileTransfers, publish_file
from .upload_paths import UPLOAD_DIRECTORY, file_folder, file_name, folder_entries

PAGE_SIZE = 200


def register_file_tools(server, access, transfers: FileTransfers) -> None:
    from pydantic import Field

    from .tool_contract import (
        ByteOffset,
        ByteSize,
        FileRevision,
        FilesPath,
        PageCursor,
        SessionIds,
        Sha256Digest,
        TransferId,
        WorkspaceDirectory,
        WorkspaceId,
        WorkspacePath,
        read_tool,
        write_tool,
    )

    @server.tool(**read_tool(
        "List Files area",
        f"List up to {PAGE_SIZE} folders and files in the Workspace Files area shown by the Files browser, "
        "sorted by name. next_after continues the listing. Use list_workspace_files for project files.",
    ))
    @file_errors
    def list_workspace_uploads(
        workspace_id: WorkspaceId, path: FilesPath = ".", after: PageCursor = None,
    ) -> dict[str, Any]:
        with access.file_gate:
            root = access.workspace(workspace_id)
            relative = relative_path(path, directory=True)
            ensure_not_protected(root / UPLOAD_DIRECTORY / relative, access.protected_folders)
            entries = folder_entries(file_folder(root, path))
        entries = sorted((entry for entry in entries if after is None or entry["name"] > after), key=lambda entry: entry["name"])
        return {
            "path": relative.as_posix(),
            "entries": entries[:PAGE_SIZE],
            "next_after": entries[PAGE_SIZE - 1]["name"] if len(entries) > PAGE_SIZE else None,
        }

    @server.tool(**write_tool(
        "Create Files folder",
        "Create a folder in the Workspace Files area inside an existing folder. Fails if the name is taken.",
        destructive=False,
    ))
    @file_errors
    def create_workspace_upload_folder(
        workspace_id: WorkspaceId,
        name: Annotated[str, Field(description="New folder name, without slashes.")],
        path: Annotated[str, Field(description="Existing parent folder relative to the Files area. '.' is its root.")] = ".",
    ) -> dict[str, Any]:
        with access.file_gate:
            root = access.workspace(workspace_id)
            relative = relative_path(path, directory=True) / file_name(name)
            ensure_not_protected(root / UPLOAD_DIRECTORY / relative, access.protected_folders)
            folder = file_folder(root, path, create=True)
            try:
                (folder / name).mkdir(mode=0o700)
            except FileExistsError:
                raise ValueError("A file or folder with this name already exists.") from None
        return {"path": relative.as_posix()}

    @server.tool(**write_tool(
        "Begin Files upload",
        "Start a chunked upload of a new file into the Workspace Files area and return a transfer_id. "
        "Fails if the path exists. Chunks go through write_workspace_chunk and the file is published by "
        "finish_workspace_upload. Use begin_workspace_file_upload for project files.",
        destructive=False,
    ))
    @file_errors
    def begin_workspace_upload(
        workspace_id: WorkspaceId, path: FilesPath, size: ByteSize, sha256: Sha256Digest,
    ) -> dict[str, Any]:
        return transfers.begin(workspace_id, path, "upload", "missing", size, sha256, upload_area=True)

    @server.tool(**read_tool(
        "List Workspace files",
        f"List up to {PAGE_SIZE} regular files and directories in a Workspace directory, including project "
        "dotfiles, with each file's size and revision. next_after continues the listing. Protected application "
        "paths are omitted.",
    ))
    @file_errors
    def list_workspace_files(
        workspace_id: WorkspaceId, path: WorkspaceDirectory = ".", after: PageCursor = None,
    ) -> dict[str, Any]:
        root = access.workspace(workspace_id)
        relative = relative_path(path, directory=True)
        directory, mode, _ = checked_path(root, relative, access.protected_folders)
        if mode != "directory":
            raise ValueError("The path must point to a directory.")
        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            if after is not None and child.name <= after:
                continue
            try:
                child_relative = relative_path((relative / child.name).as_posix())
                _, kind, info = checked_path(root, child_relative, access.protected_folders)
            except (OSError, ValueError):
                continue
            if kind != "missing":
                entry = {"path": child_relative.as_posix(), "name": child.name, "kind": kind}
                if kind == "file":
                    entry.update(file_metadata(child_relative, require_file(kind, info)))
                entries.append(entry)
            if len(entries) > PAGE_SIZE:
                break
        return {
            "path": relative.as_posix(),
            "entries": entries[:PAGE_SIZE],
            "next_after": entries[PAGE_SIZE - 1]["name"] if len(entries) > PAGE_SIZE else None,
        }

    @server.tool(**read_tool(
        "Stat Workspace file",
        "Return a regular file's byte size and revision without reading its content.",
    ))
    @file_errors
    def stat_workspace_file(workspace_id: WorkspaceId, path: WorkspacePath) -> dict[str, Any]:
        root = access.workspace(workspace_id)
        relative = relative_path(path)
        _, mode, info = checked_path(root, relative, access.protected_folders)
        return file_metadata(relative, require_file(mode, info))

    @server.tool(**read_tool(
        "Read Workspace text",
        "Read a UTF-8 Workspace file up to 128 KiB with its revision. Larger or binary files are read with "
        "begin_workspace_file_download.",
    ))
    @file_errors
    def read_workspace_text(workspace_id: WorkspaceId, path: WorkspacePath) -> dict[str, Any]:
        root = access.workspace(workspace_id)
        relative = relative_path(path)
        target, mode, info = checked_path(root, relative, access.protected_folders)
        info = require_file(mode, info)
        with open_file(target, info) as stream:
            content = stream.read(MAX_SOURCE_BYTES + 1)
        if len(content) > MAX_SOURCE_BYTES:
            raise ValueError("Use a file transfer for files over 128 KiB.")
        result = file_metadata(relative, info)
        _, mode, current = checked_path(root, relative, access.protected_folders)
        verify_revision(mode, current, result["revision"])
        result["text"] = content.decode("utf-8")
        return result

    @server.tool(**write_tool(
        "Write Workspace text",
        "Create or replace a UTF-8 Workspace file up to 128 KiB. Fails when the file changed since the given "
        "revision. Larger or binary files are written with begin_workspace_file_upload.",
        destructive=True,
    ))
    @file_errors
    def write_workspace_text(
        workspace_id: WorkspaceId,
        path: WorkspacePath,
        revision: FileRevision,
        text: Annotated[str, Field(description="Complete new file text.")],
        session_ids: SessionIds = None,
    ) -> dict[str, Any]:
        relative = relative_path(path)
        content = text.encode("utf-8")
        if len(content) > MAX_SOURCE_BYTES:
            raise ValueError("Use a file transfer for files over 128 KiB.")

        def write(root):
            target, mode, info = checked_path(root, relative, access.protected_folders)
            verify_revision(mode, info, revision)
            target.parent.mkdir(parents=True, exist_ok=True)
            _, mode, info = checked_path(root, relative, access.protected_folders)
            verify_revision(mode, info, revision)
            stage = None
            try:
                with NamedTemporaryFile(dir=target.parent, prefix=STAGING_PREFIX, delete=False) as stream:
                    stage = Path(stream.name)
                    stream.write(content)
                _, mode, info = checked_path(root, relative, access.protected_folders)
                verify_revision(mode, info, revision)
                publish_file(stage, target, mode, info)
            finally:
                if stage is not None:
                    stage.unlink(missing_ok=True)
            return file_metadata(relative, target.stat())

        return mutate_files(access, workspace_id, (relative,), session_ids or {}, write)

    @server.tool(**write_tool(
        "Move Workspace file",
        "Rename or move a regular file within one filesystem. Fails if the destination exists, except for a "
        "case-only rename. Sheet entrypoints are managed with the sheet tools.",
        destructive=True,
    ))
    @file_errors
    def move_workspace_file(
        workspace_id: WorkspaceId,
        source_path: Annotated[str, Field(description="Current forward-slash path relative to the Workspace root.")],
        destination_path: Annotated[str, Field(description="New forward-slash path relative to the Workspace root.")],
        revision: Annotated[str, Field(description="Current revision of the source file.")],
        session_ids: SessionIds = None,
    ) -> dict[str, Any]:
        source_relative, destination_relative = relative_path(source_path), relative_path(destination_path)
        if is_entrypoint(source_relative) or is_entrypoint(destination_relative):
            raise ValueError("Manage Sheet entrypoints through the Sheet commands.")

        def move(root):
            source, mode, info = checked_path(root, source_relative, access.protected_folders)
            require_file(mode, info)
            verify_revision(mode, info, revision)
            destination, mode, destination_info = checked_path(root, destination_relative, access.protected_folders)
            case_only = (
                destination_info is not None and mode == "file" and file_revision(destination_info) == revision
                and source.name != destination.name and source.name.casefold() == destination.name.casefold()
                and destination.parent.samefile(source.parent)
            )
            if mode != "missing" and not case_only:
                raise ValueError("The move destination already exists.")
            if source.parent.stat().st_dev != next(
                parent.stat().st_dev for parent in destination.parents if parent.is_dir()
            ):
                raise ValueError("Moves across filesystems are unavailable.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            _, mode, info = checked_path(root, source_relative, access.protected_folders)
            verify_revision(mode, info, revision)
            if case_only:
                _, mode, info = checked_path(root, destination_relative, access.protected_folders)
                verify_revision(mode, info, revision)
                source.rename(destination)
            else:
                _, mode, info = checked_path(root, destination_relative, access.protected_folders)
                verify_revision(mode, info, "missing")
                atomic_move(source, destination)
            return file_metadata(destination_relative, destination.stat())

        return mutate_files(access, workspace_id, (source_relative, destination_relative), session_ids or {}, move)

    @server.tool(**write_tool(
        "Trash Workspace file",
        "Move a regular file to the host's Trash or Recycle Bin. Fails when the file changed since the given "
        "revision. Sheets are removed with delete_sheet.",
        destructive=True,
    ))
    @file_errors
    def delete_workspace_file(
        workspace_id: WorkspaceId,
        path: WorkspacePath,
        revision: Annotated[str, Field(description="Current revision of the file.")],
        session_ids: SessionIds = None,
    ) -> dict[str, Any]:
        from send2trash import send2trash

        relative = relative_path(path)
        if is_entrypoint(relative):
            raise ValueError("Remove Sheet entrypoints through the Sheet commands.")

        def trash(root):
            target, mode, info = checked_path(root, relative, access.protected_folders)
            require_file(mode, info)
            verify_revision(mode, info, revision)
            send2trash(str(target))
            return {"path": relative.as_posix(), "trashed": True}

        return mutate_files(access, workspace_id, (relative,), session_ids or {}, trash)

    @server.tool(**write_tool(
        "Begin Workspace file upload",
        "Start a chunked upload that creates or replaces a project file of any size and return a transfer_id "
        "and chunk size. Chunks go through write_workspace_chunk and the file is published by "
        "finish_workspace_upload after its size and SHA-256 match.",
        destructive=True,
    ))
    @file_errors
    def begin_workspace_file_upload(
        workspace_id: WorkspaceId,
        path: WorkspacePath,
        revision: FileRevision,
        size: ByteSize,
        sha256: Sha256Digest,
    ) -> dict[str, Any]:
        return transfers.begin(workspace_id, path, "upload", revision, size, sha256)

    @server.tool(**read_tool(
        "Begin Workspace file download",
        "Start a chunked download of a project file of any size and return a transfer_id, its size, and the "
        "chunk size. Chunks are read with read_workspace_chunk and verified by finish_workspace_download.",
    ))
    @file_errors
    def begin_workspace_file_download(
        workspace_id: WorkspaceId,
        path: WorkspacePath,
        revision: Annotated[str | None, Field(
            description="Expected current revision. Omit to download whatever revision is current.",
        )] = None,
    ) -> dict[str, Any]:
        return transfers.begin(workspace_id, path, "download", revision, None, None)

    @server.tool(**read_tool(
        "Read download chunk",
        "Read up to count bytes of a download at offset as Base64 and return next_offset.",
    ))
    @file_errors
    def read_workspace_chunk(
        transfer_id: TransferId,
        offset: ByteOffset,
        count: Annotated[int, Field(description=f"Maximum bytes to read, up to {CHUNK_BYTES}.")] = CHUNK_BYTES,
    ) -> dict[str, Any]:
        return transfers.read(transfer_id, offset, count)

    @server.tool(**write_tool(
        "Write upload chunk",
        f"Append one Base64 chunk of up to {CHUNK_BYTES} decoded bytes to an upload's staged data at its "
        "current offset and return next_offset. Nothing is published until finish_workspace_upload.",
        destructive=False,
    ))
    @file_errors
    def write_workspace_chunk(
        transfer_id: TransferId,
        offset: ByteOffset,
        data: Annotated[str, Field(description="Base64-encoded chunk bytes.")],
    ) -> dict[str, Any]:
        return transfers.write(transfer_id, offset, data)

    @server.tool(**write_tool(
        "Finish upload",
        "Publish a completed upload after checking its size, SHA-256, destination revision, and sheet locks. "
        "Returns the published file's path, revision, size, and SHA-256. Repeating the call returns the same receipt.",
        destructive=True,
        idempotent=True,
    ))
    @file_errors
    def finish_workspace_upload(transfer_id: TransferId, session_ids: SessionIds = None) -> dict[str, Any]:
        return transfers.finish(transfer_id, session_ids or {}, direction="upload")

    @server.tool(**read_tool(
        "Finish download",
        "Close a fully read download and return the file's path, revision, size, and SHA-256 for verification.",
    ))
    @file_errors
    def finish_workspace_download(transfer_id: TransferId) -> dict[str, Any]:
        return transfers.finish(transfer_id, {}, direction="download")

    @server.tool(**write_tool(
        "Cancel transfer",
        "Cancel an upload or download and discard its staged data. Published files are not affected.",
        destructive=False,
    ))
    @file_errors
    def cancel_workspace_transfer(transfer_id: TransferId) -> dict[str, Any]:
        return transfers.cancel(transfer_id)
