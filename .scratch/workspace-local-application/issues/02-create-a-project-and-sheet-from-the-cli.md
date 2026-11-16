# 02: Create a project and sheet from the CLI

**What to build:** Let an AI agent initialize a project at a chosen path and create a named sheet with the same runnable Python starter as the browser's New sheet action. A person can open that project and see the sheet the agent created.

**Blocked by:** 01 (Select a Workspace and sheet from the CLI).

**Status:** resolved

- [x] The CLI initializes a new or empty directory with default Workspace settings and Python project metadata.
- [x] The CLI creates a named sheet in the selected Workspace and reports where its Python entry point was written.
- [x] CLI and browser sheet creation share the same naming, duplicate detection, and starter behavior. Initialization and sheet creation preserve existing project files on a conflict or invalid request.
- [x] A test initializes a temporary Workspace, creates a sheet from the CLI, validates and runs it, and opens the Workspace in the browser to inspect the generated node.

## Verification

The CLI initialized a new project and created a runnable sheet. The test verified duplicate and invalid name handling, preservation of existing files, and browser discovery of a sheet created while the Workspace was open.
