# 02: Share the frame lifecycle with Workflow and Node Dashboards

**What to build:** Workflow and Node Dashboard Panels use the shared Dashboard Bridge frame lifecycle while continuing to get live snapshots and setting results through their existing Flow Editor behavior.

**Blocked by:** [01: Manage the Workspace Dashboard frame lifecycle](01-manage-workspace-dashboard-frame-lifecycle.md).

**Status:** ready-for-agent

- [ ] While a flow runs, reload a Workflow or Node panel and verify the new frame receives the current in-scope snapshot and subsequent Runner events once ready.
- [ ] Switch panel scope or close and reopen during a run. Verify that displayed updates match the current panel's scope and that a setting result from a previous frame is confined to its original session.
- [ ] Compatible and undeclared panels continue to receive updates through the existing sandbox. An incompatible panel shows the provided and supported versions while the flow and compatible neighboring panels remain available.
