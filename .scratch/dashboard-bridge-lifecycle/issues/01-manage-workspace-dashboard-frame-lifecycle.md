# 01: Manage the Workspace Dashboard frame lifecycle

**What to build:** A Sheet panel placed on the Workspace Dashboard receives data from its source Sheet when the current sandboxed frame is compatible and ready, including when the source Flow Editor is unopened. A shared Dashboard Bridge module manages frame replacement and the lifetime of each panel's data updates. Each placement receives its own Sheet-scoped snapshot through Dashboard Bridge v1.

**Blocked by:** [04: Add Sheet panels to the Workspace Dashboard](../../dashboard-panel-assets/issues/04-add-sheet-panels-to-workspace-dashboard.md).

**Status:** resolved

- [x] Compatible panels, including panels without a version declaration, start receiving their own Sheet's run data after the current frame is ready. Verify both source Sheet and run ID while two sheets run with their Flow Editors unopened. An incompatible panel shows the provided and supported versions while its neighbors continue updating.
- [x] Verify that each placement displays its source run ID and timestamp for the active run and can show the latest saved data. A Node panel receives only data in its node scope. Workspace placements receive operational data only, and saved runs contain no live frames.
- [x] With a source data update delayed, reload the panel and verify that only the new frame's data session updates it. Neighboring panels continue updating.
- [x] Close a panel during a pending update and verify that its data session stops. Reopen it and verify that updates resume once for the new frame. When the source Sheet stops, its live frame clears and other panels keep updating.

## Verification

Workspace Dashboard browser tests verified live and saved Sheet-scoped snapshots for unopened sheets, node-scoped filtering, local bridge compatibility diagnostics, cancellation of stale sessions across reload and close during delayed updates, and live-frame clearing on stop.
