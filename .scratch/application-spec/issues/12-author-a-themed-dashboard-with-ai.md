# 12: Author a themed dashboard with AI

**What to build:** An AI agent can author a dashboard panel for a Workspace flow, and a developer can open it to see real run data in the Phyzflow theme.

**Blocked by:** [03: Inspect a flow step by step](03-inspect-named-flow-steps.md), [10: Share sheet editing locks between MCP and the web](10-share-sheet-editing-locks-with-mcp.md)

**Status:** resolved

**Spec requirements:** DASH-01, DASH-02

- [x] Have an AI agent write panel assets using the Live Counter example, the shared base stylesheet, and the existing dashboard subscription bridge.
- [x] Open the panel in the Workspace. Run a flow with known metrics and verify that the displayed values follow the actual run snapshot in the existing panel sandbox.
- [x] Review the panel alongside the host UI in idle, live, error, and narrow-panel states. Verify that typography, colors, spacing, chart labels, and interaction states follow the shared theme.

## Verification

Browser and MCP acceptance passed on macOS 27.0.1 with CPython 3.12.11, uv 0.7.12, and Google Chrome against the installed `workspace app` entry point:

- Added `SHARED_DASHBOARD_THEME_CSS` in `workspace/src/workspace_sdk/static/dashboard-ui.js` (covering color palette, typography/spacing tokens with `tabular-nums`, chart/reading/metric-grid defaults, `:focus-visible` and semantic `data-state` badges/banners, and `@media (max-width: 420px)` narrow-panel layout) and injected it into `frameDocument(assets)` as `<style data-phyzflow-theme="true">` before the panel's `style.css` inside the existing `sandbox="allow-scripts allow-forms"` CSP iframe.
- Added `inspect_sheet_dashboards` (`DASHBOARD_THEME_REFERENCE`) in `workspace/src/workspace_sdk/dashboard.py` and the `inspect_dashboards` MCP tool in `workspace/src/workspace_sdk/application_mcp.py`, plus Visualize drawer refresh on edit-lock completion and via `#visualize-refresh` in `workspace/src/workspace_sdk/static/index.html` and `workspace/src/workspace_sdk/static/dashboard-ui.js`.
- Verified all 39 tests in `test_application*.py` (163.285s, including all 5 tests in `workspace-test/tests/test_application_dashboard.py`), all 5 tests in `workspace-test/tests/test_architecture_deepening.py`, `node --check workspace/src/workspace_sdk/static/dashboard-ui.js`, and `git diff --check codex/phyzflow-application`.
- In `tests.test_dashboards` and `tests.test_live_counter`, the 5 current-UI tests (`test_dashboard_files_reload_and_cannot_read_workspace_or_navigate_to_network`, `test_dashboard_reconnects_after_browser_refresh`, `test_finite_generator_succeeds_after_it_has_finished`, `test_generator_failure_points_to_the_producer_after_it_yields`, `test_streams_latest_value_until_stopped_and_can_start_again`) passed; the remaining 4 tests fail on the pre-existing baseline due to removed `#visualize-workflow`, `#dashboard-run`, and `.history-run` legacy selectors.

