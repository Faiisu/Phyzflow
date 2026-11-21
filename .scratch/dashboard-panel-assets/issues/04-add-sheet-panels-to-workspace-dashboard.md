# 04: Add Sheet panels to the Workspace Dashboard

**What to build:** A user can select existing Workflow and Node Dashboard Panels from sheets in the current Workspace, arrange them in Visualize, and reopen the same placements and layout. Workspace saves the source Sheet's Workspace-relative path, panel scope, Panel ID, and layout for each placement. Use the shared Sheet lookup to resolve the saved path. All panel authoring happens in the source Sheet.

**Blocked by:** [01: Share Sheet lookup across Workspace, CLI, and MCP](../../sheet-identity/issues/01-share-sheet-lookup.md).

**Status:** ready-for-agent

- [ ] From an empty Workspace Dashboard, select existing Workflow and Node panels from two sheets. Verify that each renders its own source assets and shows its source Sheet. Toggle panels and verify full screen for the board and for one panel.
- [ ] Place panels with the same Panel ID from two different sheets. Rename one source Sheet through the application and restart Phyzflow. Verify that both placements open their original source panels, retain their positions and sizes, and display the current Sheet names.
- [ ] Use the Workspace Panels drawer to add and remove placements. The Workspace Dashboard offers a source Sheet link for editing. Edit the panel there and verify that reloading the placement shows the saved change. Remove the placement and verify that the original panel and its assets remain available in the source Sheet.
- [ ] Open a Workspace that already contains standalone Workspace Dashboard assets. Verify that the board presents Sheet placements and leaves those files on disk.
- [ ] Reject references to another Workspace. If a source panel is removed, mark its placement unavailable and leave the other placements available.
