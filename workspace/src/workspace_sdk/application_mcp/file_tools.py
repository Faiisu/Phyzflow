from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from ..node_sources import MAX_SOURCE_BYTES
from .file_paths import (
    STAGING_PREFIX, atomic_move, checked_path, file_errors, file_metadata, is_entrypoint,
    mutate_files, open_file, relative_path, require_file, revision as file_revision, verify_revision,
)
from .file_transfers import CHUNK_BYTES, FileTransfers, publish_file

PAGE_SIZE = 200


def register_file_tools(server, access, transfers: FileTransfers) -> None:
    @server.tool(description="List regular files and directories in a granted Workspace, including project dotfiles.")
    @file_errors
    def list_workspace_files(workspace_id: str, path: str = ".", after: str | None = None) -> dict:
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

    @server.tool(description="Read regular-file size and revision using a Workspace-relative path.")
    @file_errors
    def stat_workspace_file(workspace_id: str, path: str) -> dict:
        root = access.workspace(workspace_id)
        relative = relative_path(path)
        _, mode, info = checked_path(root, relative, access.protected_folders)
        return file_metadata(relative, require_file(mode, info))

    @server.tool(description="Read a UTF-8 file up to 128 KiB using a Workspace-relative path.")
    @file_errors
    def read_workspace_text(workspace_id: str, path: str) -> dict:
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

    @server.tool(description="Create or replace a UTF-8 Workspace file up to 128 KiB with its expected revision.")
    @file_errors
    def write_workspace_text(
        workspace_id: str, path: str, revision: str, text: str, session_ids: dict[str, str] | None = None,
    ) -> dict:
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

    @server.tool(description="Rename or move a regular Workspace file without overwriting its destination.")
    @file_errors
    def move_workspace_file(
        workspace_id: str, source_path: str, destination_path: str, revision: str,
        session_ids: dict[str, str] | None = None,
    ) -> dict:
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

    @server.tool(description="Send a regular Workspace file with the expected revision to the host's Trash.")
    @file_errors
    def delete_workspace_file(
        workspace_id: str, path: str, revision: str, session_ids: dict[str, str] | None = None,
    ) -> dict:
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

    @server.tool(description="Begin a streamed upload or download of an opaque Workspace file.")
    @file_errors
    def begin_workspace_transfer(
        workspace_id: str, path: str, direction: Literal["upload", "download"],
        revision: str | None = None, size: int | None = None, sha256: str | None = None,
    ) -> dict:
        return transfers.begin(workspace_id, path, direction, revision, size, sha256)

    @server.tool(description="Read the next Base64 chunk of a Workspace download at its byte offset.")
    @file_errors
    def read_workspace_chunk(transfer_id: str, offset: int, count: int = CHUNK_BYTES) -> dict:
        return transfers.read(transfer_id, offset, count)

    @server.tool(description="Write the next Base64 chunk of a Workspace upload at its byte offset.")
    @file_errors
    def write_workspace_chunk(transfer_id: str, offset: int, data: str) -> dict:
        return transfers.write(transfer_id, offset, data)

    @server.tool(description="Finalize a Workspace file transfer and return its SHA-256 and file metadata.")
    @file_errors
    def finish_workspace_transfer(transfer_id: str, session_ids: dict[str, str] | None = None) -> dict:
        return transfers.finish(transfer_id, session_ids or {})

    @server.tool(description="Cancel a Workspace file transfer and discard staged data.")
    @file_errors
    def cancel_workspace_transfer(transfer_id: str) -> dict:
        return transfers.cancel(transfer_id)
