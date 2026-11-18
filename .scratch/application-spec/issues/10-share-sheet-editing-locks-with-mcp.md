# 10: Share sheet editing locks between MCP and the web

**What to build:** An authorized editor can edit a sheet exclusively while others follow it read-only, and MCP returns the sheet to an editable state after clean closure.

**Blocked by:** [09: Grant and revoke MCP Workspace access](09-grant-and-revoke-mcp-workspace-access.md)

**Status:** resolved

**Spec requirements:** EDIT-01, EDIT-02

- [x] Open a sheet for editing through MCP. From another authorized session, view the sheet read-only and verify that writes through either the web or MCP are rejected. Verify that an unlocked sheet remains editable.
- [x] Edit a sheet through the web and verify that a second MCP editing session cannot write to that sheet.
- [x] Close an MCP editing session after success, failure, and cancellation. Verify that its operation-owned processes exit and the lock is released after closure.
- [x] Hand off a flow run to the Workspace and close the editing session. Verify that the Workspace continues to manage the run.

## Verification

Browser and MCP acceptance passed on macOS 27.0.1 with CPython 3.12.11, uv 0.7.12, and Google Chrome. All 34 tests in `test_application*.py` passed in 146.817s against the installed `workspace app` entry point, including all 5 tests in `workspace-test/tests/test_application_edit_locks.py`.

- Opening a sheet (`Main`) for editing via MCP (`open_sheet(mode="edit")`) acquires an exclusive lock under `<sheet_root>/.workspace/edit-lock.json` (`SheetEditLease`); a second MCP client and Chrome browsers view the sheet read-only (with read-only notices, disabled Save, and HTTP `409` / `ToolError` write rejection) while an unlocked sibling sheet (`Second`) remains editable from both MCP and Chrome.
- Editing a sheet through the web (`Edit flow.py` or `View files` / `node-source`) acquires a web editing lock that blocks MCP `open_sheet(mode="edit")`, `write_file`, and `apply_patch` as well as a second browser session, and releases the lock cleanly when the web editor closes.
- Closing an MCP editing session (`close_sheet`) after validation success, validation failure, and cancellation (both concurrent `close_sheet(outcome="cancelled")` and HTTP/task abort during `validate_flow`) terminates and waits for supervised `.venv` operation processes and descendant process groups before releasing the lock.
- Handing off a flow run via `run_flow` and closing the editing session (`close_sheet`) releases the editing lock immediately while `FlowSheet` continues managing the run until completion or `stop_flow`.
- `node --check workspace/src/workspace_sdk/static/workspace.js`, `node --check workspace/src/workspace_sdk/static/app.js`, and `git diff --check codex/phyzflow-application` all passed.

Run from `workspace-test` with `./workspace[mcp]` and `./workspace-test[verification]` installed:

```sh
node --check ../workspace/src/workspace_sdk/static/workspace.js
node --check ../workspace/src/workspace_sdk/static/app.js
/tmp/phyzflow-app-10-venv/bin/python -m unittest discover -s tests -p 'test_application*.py' -v
```

