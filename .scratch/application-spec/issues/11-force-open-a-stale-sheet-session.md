# 11: Recover a stuck sheet with Force Open

**What to build:** An administrator can inspect a possibly stale MCP editing session and decide whether to reopen the sheet from its latest saved source.

**Blocked by:** [10: Share sheet editing locks between MCP and the web](10-share-sheet-editing-locks-with-mcp.md)

**Status:** resolved

**Spec requirements:** EDIT-03

- [x] Keep an idle MCP session connected and sending heartbeats every 15 seconds. Verify that it keeps its lock. Stop heartbeats for 45 seconds or simulate an unclean disconnect and verify that the UI marks the session possibly stale.
- [x] Verify the UI shows the lock owner and last heartbeat and explains what happens to unsaved changes. Verify that the lock remains in place until the user decides.
- [x] Save source, leave a later change unsaved, and force open the stuck sheet. Verify that editing processes are cleaned up, the last saved source loads, and the old session cannot write after reconnecting.

## Verification

Browser and MCP acceptance passed on macOS with CPython 3.12.11 and Google Chrome. All 38 tests in `test_application*.py` passed in 175.757s against the installed `workspace app` entry point, including all 4 tests in `workspace-test/tests/test_application_force_open.py` and all 5 tests in `workspace-test/tests/test_application_edit_locks.py`.

- Idle MCP sessions sending `heartbeat_sheet` every 15 seconds retain their sheet lock across >60 seconds without becoming stale; stopping heartbeats for 45 seconds or aborting an in-flight `validate_flow` call (`mark_disconnected`) marks the session `Possibly stale` (and `Disconnected` on unclean disconnect) in the Workspace bar, `Edit flow.py`, and node files views.
- Clicking **Force Open** opens the confirmation dialog showing the lock owner, formatted UTC last heartbeat timestamp, session status (`Possibly stale` / `Active` / `Possibly stale (Disconnected)`), and the effect on unsaved edits; cancelling leaves the lock and read-only enforcement untouched.
- Staging an unsaved draft (`stage_draft`), leaving a supervised `.venv` `validate_flow` process running with a SIGTERM-ignoring child, and confirming **Force Open** terminates and reaps the operation-owned editing process tree, discards the unsaved draft, reloads the latest saved source from disk into both the flow frame and web editor, enables web editing/saving, and persists old session invalidation in `.workspace/edit-lock-sessions.json` so reconnecting `heartbeat_sheet`, `stage_draft`, `write_file`, `apply_patch`, `validate_flow`, and `close_sheet` calls are rejected even after an application restart.
- `node --check workspace/src/workspace_sdk/static/workspace.js`, `node --check workspace/src/workspace_sdk/static/app.js`, and `git diff --check codex/phyzflow-application` all passed.

Run from `workspace-test` with `./workspace[mcp]` and `./workspace-test[verification]` installed:

```sh
node --check ../workspace/src/workspace_sdk/static/workspace.js
node --check ../workspace/src/workspace_sdk/static/app.js
/tmp/phyzflow-app-11-venv/bin/python -m unittest discover -s tests -p 'test_application*.py' -v
```

