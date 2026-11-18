# 13: Complete the MCP flow-development commands

**What to build:** An authorized MCP client can complete the same flow-development work available in the web application within its granted Workspace.

**Blocked by:** [04: Use saved source on the next run](04-run-the-latest-saved-source.md), [05: Run and stop sheets independently](05-run-all-and-stop-sheets-independently.md), [06: Save and expire console logs per sheet](06-save-and-expire-sheet-console-logs.md), [12: Author a themed dashboard with AI](12-author-a-themed-dashboard-with-ai.md)

**Status:** resolved

**Spec requirements:** MCP-01, MCP-02, MCP-03

- [x] Compare the flow-development commands available in the web app with the MCP command list. Exercise source and dashboard work, validation, running and stopping, inspecting execution and logs, and installing a test dependency into the shared Workspace environment through MCP.
- [x] Verify that saved code and installed dependencies affect the expected sheet runs and that the deployed instance enforces Workspace permissions for each operation.
- [x] Use representative denied operations to verify that the same token cannot change the Phyzflow application's source or specification or update or restart the deployed application itself.
- [x] Record evidence through an actual MCP client, including successful and denied operations.

## Verification

Browser and Streamable HTTP MCP acceptance passed on macOS 27.0.1 with CPython 3.12.11 and Google Chrome against the installed `workspace app` entry point (`./workspace[mcp]` installed into `/tmp/phyzflow-app-13-venv`):

- All 46 tests in `workspace-test/tests/test_application*.py` passed in 204.856s, including all 3 tests in `workspace-test/tests/test_application_mcp_commands.py` (`test_mcp_tool_catalog_and_workspace_grant_enforcement`, `test_mcp_sheet_management_files_search_and_boundary_protection`, `test_mcp_full_flow_development_lifecycle_and_ui_parity`) and `test_application_mcp.py`.
- All 5 tests in `workspace-test/tests/test_architecture_deepening.py` passed in 0.084s.
- Verified with an actual `mcp.Client` over Streamable HTTP `/mcp` and Playwright Chrome that:
  - The MCP catalog exposes the complete flow-development tool set (`list_workspaces`, `inspect_workspace`, `create_sheet`, `delete_sheet`, `inspect_dashboards`, `open_sheet`, `heartbeat_sheet`, `stage_draft`, `close_sheet`, `list_files`, `read_file`, `search_code`, `write_file`, `apply_patch`, `read_node_settings`, `update_node_setting`, `validate_flow`, `run_flow`, `run_all_flows`, `stop_flow`, `inspect_execution`, `read_console_logs`, `configure_console_logs`, `install_dependencies`) while keeping `run_command`, `update_workspace`, and `restart_workspace` absent and rejected.
  - Sheet lifecycle (`create_sheet`, `delete_sheet`), source/dashboard authoring, syntax/import validation, shared `.venv` wheel installation affecting both `Main` and a secondary sheet, `NodeSetting` persistence and live updates, `run_flow`/`run_all_flows`/`stop_flow`, `inspect_execution` step timing/metrics/error attribution/history, and `configure_console_logs`/`read_console_logs` (saved and live-only) all work end-to-end and match the web UI.
  - Ungranted Workspaces, live grant revocation, protected folders (`state_dir` and the Phyzflow checkout including `docs/application-spec.md` and `workspace/`, detected even on non-editable `./workspace[mcp]` installs via `direct_url.json`), path traversal, symlinks, hidden/internal paths, and package flag/path injection are rejected.
- `node --check workspace/src/workspace_sdk/static/workspace.js` and `git diff --check codex/phyzflow-application -- workspace/ workspace-test/ .scratch/` passed cleanly.

Reproduce from `workspace-test`:

```sh
/tmp/phyzflow-app-13-venv/bin/python -m unittest discover -s tests -p 'test_application*.py' -v
```

