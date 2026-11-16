# 01: Select a Workspace and sheet from the CLI

**What to build:** Let an AI agent or developer select an existing Workspace by path, discover its sheets, and validate or run the selected Python flow from any working directory. The same project selection opens that Workspace in the browser.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] An explicit project path works for opening the UI and listing sheets. The list identifies the root flow as Main when present and agrees with the browser's sheet labels.
- [x] The CLI can run `check` and `run` on a named sheet using the same flow validation, source diagnostics, and input and output contracts as direct CLI execution from its directory.
- [x] The UI reads settings from the selected Workspace. Flow execution resolves project-relative files from the selected flow directory.
- [x] Missing project paths and unknown sheet names fail with actionable diagnostics. Existing commands that infer the project from the working directory still work.
- [x] An end-to-end test uses an unrelated working directory to discover, validate, run, and open a Workspace containing more than one sheet.

## Verification

The installed CLI was exercised from an unrelated working directory. The test verifies Main and a named sheet, project-relative file access during execution, diagnostics for invalid selection, and project settings in the browser.
