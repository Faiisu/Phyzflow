# 07. Compose a Workspace dashboard from multiple sheets

Status: resolved

Add a Workspace panel that selects saved run fields and live or saved metrics from multiple sheets. Follow [DASH-03](../../../docs/application-spec.md#dashboard-panels). Use the existing Dashboard Bridge and the same sandbox as workflow and node panels.

- [x] Compare the latest saved durations of Main and Hand Detection in one chart.
- [x] Display numeric metrics from concurrent runs and verify the source sheet, run ID, and timestamp.
- [x] Continue displaying one source when the other stops or is removed. Reject sources from another Workspace.
- [x] Resize the panel without changing the browser viewport. Verify that existing Bridge v1 panels still load.

The [browser acceptance test](../../../workspace-test/tests/test_workspace_dashboard.py) exercises the comparison and live data with fixture flows. The [authoring guide](../../../workspace/README.md#build-a-dashboard-from-multiple-sheets) documents the source declaration and links to the example panel.
