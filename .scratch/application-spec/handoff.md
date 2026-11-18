# macOS implementation handoff

Continue on `codex/phyzflow-application` in the Phyzflow checkout. The starting commit was `64a2643`. Read [AGENTS.md](../../AGENTS.md), the [development plan](../../docs/development-plan.md), the [application requirements](../../docs/application-spec.md), and the [ticket graph](spec.md) before picking up work. The current pass targets macOS. Keep new code comment-free.

## Integrated behavior

- [01](issues/01-create-and-run-a-workspace.md) and [02](issues/02-add-an-existing-workspace.md) cover Workspace creation, enrollment, restart persistence, and shared Python 3.12 environments. See `application.py`, `workspace_environment.py`, and the landing assets under `workspace/src/workspace_sdk/`.
- [03](issues/03-inspect-named-flow-steps.md) and [05](issues/05-run-all-and-stop-sheets-independently.md) cover step timing, live progress and metrics, failure attribution, Run All, live consoles, and independent stop. See `workspace_ui.py`, `sheet_runs.py`, `sheet_worker.py`, `agent_supervisor.py`, `web.py`, and the flow and Workspace UI assets under `workspace/src/workspace_sdk/`.
- [04](issues/04-run-the-latest-saved-source.md) covers saving Python during an active run, preserving the original source for existing files including lazy imports, and running the latest saved source on the next Run. See `project.py`, `sheets.py`, `sheet_worker.py`, and the editor and reload routes. File deletion and rename during an active run were outside the observed cases.
- [08](issues/08-sign-in-as-the-built-in-administrator.md) and [09](issues/09-grant-and-revoke-mcp-workspace-access.md) cover built-in administrator sign-in and immediate MCP grant changes and revocation. See `application_auth.py`, `application_mcp.py`, `sign-in.html`, and `mcp-access.js`.
- [14](issues/14-restart-unexpectedly-terminated-runs.md) covers up to three automatic restarts after an execution-process crash. See `FlowSheet` in `workspace_ui.py` and `SheetRun` in `sheet_runs.py`.

The [SDK README](../../workspace/README.md) documents `workspace app`, administrator bootstrap, and the installed app's `/mcp` endpoint. Install `./workspace[mcp]` in the application environment. The legacy `workspace ui` and Edge MCP paths still have their own entry points.

## Resume from the ready tickets

- **06** should persist the explicit console output captured in `web.py` through `capture_console`. Keep stream and run IDs, daily rotation, per-sheet configuration, and retention distinct from sanitized run history.
- **07** should fence new work and verify process-tree exit before sending the Workspace to macOS Trash. The existing `workspace_routes` cleanup callback stops workers. Prove descendant exit before committing the registry change and cover Trash rejection.
- **10** should share sheet editing sessions across web and MCP. Scope every MCP operation through `WorkspaceAccess` in `application_mcp.py`. Its `sheet_resolver(workspace_id)` returns a callable that resolves a `FlowSheet`. Keep editor ownership independent of run admission and use explicit session close and heartbeat. This opens **11** and **12**. **13** extends the installed app's typed MCP tools after the source, lifecycle, console, and dashboard work is ready. The permission boundary for running user Python and installing dependencies still requires verification.
- **15** should persist run intent at `FlowSheet._start_attempt` before starting the collector, track terminal completion by generation, and distinguish `stop(shutdown=True)` from explicit Stop. Persist any replay input separately from sanitized diagnostics. Restore before any browser visit. This opens **17** for headless macOS startup and reboot testing.

Linux **16** and Windows **18** are deferred by scope. **19** currently lists Linux systemd **16** as a prerequisite. Confirm the macOS installed service path for an HTTPS pass before advancing it. The full platform, remote client, reboot, and device criteria in **20** remain open. A real macOS LaunchDaemon reboot test still needs a suitable test host and an administrator installation path. Noninteractive `sudo` on this workstation requires credentials.

## Verification

All 15 installed application tests passed at `0ae5b1e` in 59.5 seconds using Chrome and an actual MCP Python SDK client on macOS 27.0.1 with Python 3.12.11. JavaScript syntax checks and `git diff --check 7e424f3..0ae5b1e` passed. The combined test log is `/tmp/phyzflow-acceptance.eOHJkoXq/handoff.log` on this host. Reproduce the application suite from the repository root in a fresh environment.

```sh
test_dir=$(mktemp -d)
uv venv --python 3.12 "$test_dir/venv"
uv pip install --python "$test_dir/venv/bin/python" './workspace[mcp]' './workspace-test[verification]'
cd workspace-test
"$test_dir/venv/bin/python" -m unittest discover -s tests -p 'test_application*.py' -v
```

The original `64a2643` full test run reported 102 tests with 4 failures and 12 errors, including the absent optional Torch dependency. Installing Torch and running `test_runner.py` separately passed all five tests. Legacy UI failures reproduced on the baseline include the disabled Reload control during an active run and missing dashboard/history controls. The original full log is `/tmp/phyzflow-acceptance.eOHJkoXq/baseline.log` on this host. The two-axis integration review is pending until the remaining work is implemented.

The original working copy contains user edits in `README.md` and deletions of `run-workspace.bat` and `run-workspace.sh`. These edits are outside the integration commits. The tracker uses local Markdown and the integration branch is local.
