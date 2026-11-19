# macOS implementation handoff

Continue on `codex/phyzflow-application` in the Phyzflow checkout. The starting commit was `64a2643`, and the previous handoff was `0eb4305`. Read [AGENTS.md](../../AGENTS.md), the [application requirements](../../docs/application-spec.md), and the [ticket graph](spec.md) before picking up work. The current pass targets macOS. Keep new code comment-free.

## Integrated behavior

- [01](issues/01-create-and-run-a-workspace.md) and [02](issues/02-add-an-existing-workspace.md) cover Workspace creation, enrollment, restart persistence, and shared Python 3.12 environments. See `application.py`, `workspace_environment.py`, and the landing assets under `workspace/src/workspace_sdk/`.
- [03](issues/03-inspect-named-flow-steps.md) and [05](issues/05-run-all-and-stop-sheets-independently.md) cover step timing, live progress and metrics, failure attribution, Run All, live consoles, and independent stop. See `workspace_ui.py`, `sheet_runs.py`, `sheet_worker.py`, `agent_supervisor.py`, `web.py`, and the flow and Workspace UI assets under `workspace/src/workspace_sdk/`.
- [04](issues/04-run-the-latest-saved-source.md) covers saving Python during an active run, preserving the original source for existing files including lazy imports, and running the latest saved source on the next Run. See `project.py`, `sheets.py`, `sheet_worker.py`, and the editor and reload routes. File deletion and rename during an active run were outside the observed cases.
- [06](issues/06-save-and-expire-sheet-console-logs.md) covers per-sheet console log persistence, live console fallback when saving is off, daily segment rotation, and retention expiry across quiet periods. See `console.py`, `web.py`, and `static/{index.html,app.css,app.js}`.
- [07](issues/07-delete-a-workspace-safely.md) fences new work, stops and verifies process-tree exit before moving the Workspace to macOS Trash, reports active host-OS processes or Trash rejections, and unregisters only after a successful move. See `application.py`, `workspace_ui.py`, `application_mcp.py`, and `static/{landing.html,landing.js,workspace.css}`.
- [08](issues/08-sign-in-as-the-built-in-administrator.md) and [09](issues/09-grant-and-revoke-mcp-workspace-access.md) cover built-in administrator sign-in and immediate MCP grant changes and revocation. See `application_auth.py`, `application_mcp.py`, `sign-in.html`, and `mcp-access.js`.
- [10](issues/10-share-sheet-editing-locks-with-mcp.md) and [11](issues/11-force-open-a-stale-sheet-session.md) cover shared exclusive sheet editing locks across web and MCP, 15s heartbeats, 45s stale and unclean-disconnect detection, process cleanup on close/abort, unsaved session drafts, and Force Open recovery from the latest saved source on disk. See `sheet_locks.py`, `application_mcp.py`, `workspace_ui.py`, `web.py`, and `static/{workspace.html,workspace.js,workspace.css,index.html,app.js}`.
- [12](issues/12-author-a-themed-dashboard-with-ai.md) injects `SHARED_DASHBOARD_THEME_CSS` into sandboxed dashboard iframes, adds `inspect_dashboards` over MCP, and refreshes the Visualize drawer when editing locks release. See `dashboard.py`, `application_mcp.py`, `static/dashboard-ui.js`, and `static/index.html`.
- [13](issues/13-complete-mcp-flow-development-commands.md) completes the typed flow-development MCP command set (`create_sheet`, `delete_sheet`, `open_sheet`, `heartbeat_sheet`, `stage_draft`, `close_sheet`, `list_files`, `read_file`, `search_code`, `write_file`, `apply_patch`, `inspect_dashboards`, `read_node_settings`, `update_node_setting`, `validate_flow`, `run_flow`, `run_all_flows`, `stop_flow`, `inspect_execution`, `read_console_logs`, `configure_console_logs`, `install_dependencies`) while keeping `run_command`, `update_workspace`, `restart_workspace`, application state, and the Phyzflow source checkout outside the MCP boundary. See `application_mcp.py`, `workspace_environment.py`, `application.py`, `workspace_ui.py`, and `workspace/README.md`.
- [14](issues/14-restart-unexpectedly-terminated-runs.md) and [15](issues/15-restore-work-after-phyzflow-restarts.md) cover up to three automatic in-service restarts after an execution-process crash and durable `RunStateStore` restoration of active one-time, automation, and continuous runs on backend startup before any browser visit, while keeping stopped and terminal runs un-replayed. See `sheet_runs.py`, `workspace_ui.py`, `application.py`, and `static/workspace.js`.

The [SDK README](../../workspace/README.md) documents `workspace app`, administrator bootstrap, and the installed app's `/mcp` endpoint. Install `./workspace[mcp]` in the application environment. The legacy `workspace ui` and Edge MCP paths still have their own entry points.

## Remaining deployment and release tickets

- **16** (Linux systemd) and **18** (Windows Task Scheduler) are deferred by the macOS-first scope.
- **17** (macOS LaunchDaemon boot and real-reboot test), **19** (Caddy HTTPS over loopback with remote browser/MCP verification, currently listing 16 as a prerequisite), and **20** (cross-platform release acceptance, real reboots, remote clients, and peripheral/board access) remain open (`ready-for-agent`) and require target hosts/administrator boot privileges.

## Verification

A two-axis code review (`Standards` and `Spec`) was run on `0eb4305..819e529` and its findings (validation location reporting in `sheet_worker.py --check`, unbounded `invalidated` session tracking in `sheet_locks.py`, and shared `spawn_supervised` / `is_protected_path` helpers) were resolved and merged at `6bfd0e9`.

All 47 installed application tests passed at `6bfd0e9` (verified via `c5c45b4`) in 207.1 seconds using Google Chrome and an actual MCP Python SDK client on macOS 27.0.1 with Python 3.12.11. JavaScript syntax checks (`node --check`) on all static `.js` files and `git diff --check` passed. Reproduce the application suite from the repository root in a fresh environment:

```sh
test_dir=$(mktemp -d)
uv venv --python 3.12 "$test_dir/venv"
uv pip install --python "$test_dir/venv/bin/python" './workspace[mcp]' './workspace-test[verification]'
cd workspace-test
"$test_dir/venv/bin/python" -m unittest discover -s tests -p 'test_application*.py' -v
```

The original working copy contains pre-existing unstaged edits (`README.md`, deleted `docs/development-plan.md`, `run-workspace.bat`, `run-workspace.sh`) outside the integration commits.
