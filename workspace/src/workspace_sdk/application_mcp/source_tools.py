import os
import stat
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any

from ..node_sources import MAX_SOURCE_BYTES, SourceChanged, edit_source_file
from ..sheet_locks import SheetLockedError
from ..workspace_environment import is_protected_path
from .access import (
    EDITABLE_EXTENSIONS,
    WorkspaceAccess,
    resolve_sheet_file,
    resolve_workspace_sheet,
)

MAX_ENTRIES = 500
MAX_SCAN_FILES = 2000
MAX_SEARCH_MATCHES = 100


def register_source_tools(server, access: WorkspaceAccess) -> None:
    from mcp.server.mcpserver.exceptions import ToolError
    from pydantic import Field

    from .tool_contract import (
        OptionalEditSession,
        RequiredEditSession,
        SheetName,
        SheetPath,
        SheetRevision,
        WorkspaceId,
        read_tool,
        write_tool,
    )

    @server.tool(**read_tool(
        "List sheet files",
        f"List up to {MAX_ENTRIES} immediate files and subdirectories of a sheet directory, skipping hidden "
        "entries, caches, and environments. Use list_workspace_files for paths outside a sheet or for dotfiles.",
    ))
    def list_files(
        workspace_id: WorkspaceId,
        path: Annotated[str, Field(
            description="Directory relative to the sheet directory. '.' is the sheet root.",
        )] = ".",
        sheet: SheetName = "Main",
    ) -> dict[str, Any]:
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        target, relative = resolve_sheet_file(
            root,
            flow_sheet,
            path,
            for_write=False,
            allow_root=True,
            protected_folders=access.protected_folders,
        )
        if not target.is_dir() or target.is_symlink():
            raise ToolError("Expected an accessible directory inside the sheet.")
        base = root if (flow_sheet.main and relative.parts and relative.parts[0] == "flows") else flow_sheet.root.resolve()
        entries = []
        truncated = False
        try:
            with os.scandir(target) as children:
                items = sorted(children, key=lambda child: child.name)
        except OSError as error:
            raise ToolError(f"Could not list {relative.as_posix()}: {error.strerror or error}.") from None
        for child in items:
            if child.name.startswith(".") or child.name == "__pycache__" or child.is_symlink():
                continue
            is_dir = child.is_dir(follow_symlinks=False)
            is_file = child.is_file(follow_symlinks=False)
            if not is_dir and not is_file:
                continue
            child_path = Path(child.path)
            if is_dir and (child_path / "pyvenv.cfg").is_file():
                continue
            if len(entries) == MAX_ENTRIES:
                truncated = True
                break
            entries.append({
                "path": child_path.relative_to(base).as_posix(),
                "kind": "directory" if is_dir else "file",
            })
        return {
            "sheet": sheet,
            "path": relative.as_posix(),
            "entries": entries,
            "truncated": truncated,
        }

    @server.tool(**read_tool(
        "Read sheet file",
        "Read a file in a sheet directory as UTF-8 text with the revision that write_file and apply_patch expect. "
        "Works while another editor holds the lock. Files over 128 KiB return truncated content and no revision.",
    ))
    def read_file(workspace_id: WorkspaceId, path: SheetPath, sheet: SheetName = "Main") -> dict[str, Any]:
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        target, relative = resolve_sheet_file(
            root,
            flow_sheet,
            path,
            for_write=False,
            protected_folders=access.protected_folders,
        )
        if not target.is_file():
            raise ToolError("Only regular files can be read.")
        try:
            with target.open("rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ToolError("Only regular files can be read.")
                data = stream.read(MAX_SOURCE_BYTES + 1)
        except OSError as error:
            raise ToolError(f"Could not read {relative.as_posix()}: {error.strerror or error}.") from None
        truncated = len(data) > MAX_SOURCE_BYTES
        content_bytes = data[:MAX_SOURCE_BYTES]

        return {
            "path": relative.as_posix(),
            "sheet": sheet,
            "content": content_bytes.decode("utf-8", errors="replace"),
            "revision": None if truncated else sha256(content_bytes).hexdigest(),
            "truncated": truncated,
        }

    @server.tool(**read_tool(
        "Search sheet code",
        f"Find lines containing a literal, case-sensitive string in a sheet's source and dashboard files. "
        f"Returns up to {MAX_SEARCH_MATCHES} matches with path and line number; truncated is true when more exist.",
    ))
    def search_code(
        workspace_id: WorkspaceId,
        query: Annotated[str, Field(description="Literal text to find, 1 to 512 characters. Not a regular expression.")],
        sheet: SheetName = "Main",
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not 1 <= len(query) <= 512:
            raise ToolError("query must contain 1 to 512 characters.")
        _, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        sheet_root = flow_sheet.root.resolve()
        matches: list[dict] = []
        scanned = 0
        truncated = False
        pending = [sheet_root]
        while pending:
            directory = pending.pop()
            try:
                with os.scandir(directory) as children:
                    items = sorted(children, key=lambda child: child.name, reverse=True)
            except OSError:
                continue
            for child in items:
                if scanned >= MAX_SCAN_FILES:
                    return {"sheet": sheet, "matches": matches, "truncated": True}
                scanned += 1
                if child.is_symlink() or child.name.startswith(".") or child.name == "__pycache__":
                    continue
                child_path = Path(child.path)
                if flow_sheet.main and directory == sheet_root and child.name == "flows":
                    continue
                if is_protected_path(child_path, access.protected_folders):
                    continue
                if child.is_dir(follow_symlinks=False):
                    if not (child_path / "pyvenv.cfg").is_file():
                        pending.append(child_path)
                    continue
                if not child.is_file(follow_symlinks=False) or child_path.suffix not in EDITABLE_EXTENSIONS:
                    continue
                try:
                    with child_path.open("rb") as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            continue
                        data = stream.read(MAX_SOURCE_BYTES + 1)
                except OSError:
                    continue
                if len(data) > MAX_SOURCE_BYTES:
                    truncated = True
                    data = data[:MAX_SOURCE_BYTES]
                relative = child_path.relative_to(sheet_root).as_posix()
                text_content = data.decode("utf-8", errors="replace")
                for line_no, text in enumerate(text_content.splitlines(), 1):
                    if query in text:
                        if len(text) > 1024:
                            truncated = True
                        matches.append({"path": relative, "line": line_no, "text": text[:1024]})
                        if len(matches) >= MAX_SEARCH_MATCHES:
                            return {"sheet": sheet, "matches": matches, "truncated": True}
        return {"sheet": sheet, "matches": matches, "truncated": truncated}

    def perform_sheet_edit(
        workspace_id: str,
        sheet: str,
        path: str,
        revision: str,
        session_id: str | None,
        build_source,
    ) -> dict:
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        target, relative = resolve_sheet_file(
            root,
            flow_sheet,
            path,
            for_write=True,
            protected_folders=access.protected_folders,
        )
        if not isinstance(revision, str) or not revision:
            raise ToolError("An expected revision is required.")
        principal = access.principal()

        def apply_edit() -> dict:
            exists = target.exists()
            if not exists and revision != "missing":
                raise SourceChanged
            before = edit_source_file(target, None, None, initial="" if not exists else None)
            if before["revision"] != revision:
                raise SourceChanged
            source = build_source(before["source"], exists)
            if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
                raise ValueError("Source must fit in 128 KiB.")
            target.parent.mkdir(parents=True, exist_ok=True)
            updated = edit_source_file(target, source, revision, initial="" if not exists else None)
            return {
                "path": relative.as_posix(),
                "sheet": sheet,
                "revision": updated["revision"],
                "created": not exists,
            }

        try:
            return flow_sheet.edit_lease.write_locked(
                principal, session_id, apply_edit, path=relative.as_posix(),
            )
        except SourceChanged:
            raise ToolError("Source changed. Call read_file for the latest revision, then retry the edit.") from None
        except (SheetLockedError, ValueError, UnicodeError, SyntaxError, OSError) as error:
            raise ToolError(str(error)) from None

    @server.tool(**write_tool(
        "Stage draft",
        "Record unsaved draft text for a file in the active editing session so a human recovering the session "
        "can review it. Does not change the file on disk; use write_file to save.",
        destructive=False,
        idempotent=True,
    ))
    def stage_draft(
        workspace_id: WorkspaceId,
        path: SheetPath,
        source: Annotated[str, Field(description="Complete draft text of the file, up to 128 KiB.")],
        sheet: SheetName = "Main",
        session_id: RequiredEditSession = "",
    ) -> dict[str, Any]:
        if not isinstance(source, str):
            raise ToolError("source must be text.")
        root, flow_sheet = resolve_workspace_sheet(access, workspace_id, sheet)
        _, relative = resolve_sheet_file(
            root,
            flow_sheet,
            path,
            for_write=True,
            protected_folders=access.protected_folders,
        )
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ToolError("Source must fit in 128 KiB.")
        try:
            lock_status = flow_sheet.edit_lease.stage_draft(
                access.principal(), session_id, relative.as_posix(), source,
            )
        except (SheetLockedError, ValueError, OSError) as error:
            raise ToolError(str(error)) from None
        return {
            "path": relative.as_posix(),
            "sheet": sheet,
            "staged": True,
            "edit_lock": lock_status,
        }

    @server.tool(**write_tool(
        "Write sheet file",
        "Create or fully replace a source or dashboard file in a sheet. Fails when the file changed since the given "
        "revision. Returns the new revision. Use apply_patch to change one passage of an existing file.",
        destructive=True,
    ))
    def write_file(
        workspace_id: WorkspaceId,
        path: SheetPath,
        revision: SheetRevision,
        source: Annotated[str, Field(description="Complete new file text, up to 128 KiB.")],
        sheet: SheetName = "Main",
        session_id: OptionalEditSession = None,
    ) -> dict[str, Any]:
        if not isinstance(source, str):
            raise ToolError("source must be text.")
        return perform_sheet_edit(workspace_id, sheet, path, revision, session_id, lambda _before, _exists: source)

    @server.tool(**write_tool(
        "Patch sheet file",
        "Replace the single exact occurrence of old_text in an existing sheet file. Fails when old_text matches "
        "zero or several places, or when the file changed since the given revision. Returns the new revision.",
        destructive=True,
    ))
    def apply_patch(
        workspace_id: WorkspaceId,
        path: SheetPath,
        revision: Annotated[str, Field(description="Revision returned by read_file for the current file.")],
        old_text: Annotated[str, Field(description="Exact existing text to replace. It must occur exactly once.")],
        new_text: Annotated[str, Field(description="Replacement text. May be empty to delete old_text.")],
        sheet: SheetName = "Main",
        session_id: OptionalEditSession = None,
    ) -> dict[str, Any]:
        if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
            raise ToolError("A patch requires nonempty old_text and new_text.")

        def replace_once(before_source: str, exists: bool) -> str:
            if not exists:
                raise SourceChanged
            if before_source.count(old_text) != 1:
                raise ValueError("old_text must match exactly one location.")
            return before_source.replace(old_text, new_text, 1)

        return perform_sheet_edit(workspace_id, sheet, path, revision, session_id, replace_once)
