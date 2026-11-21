# 01: Share Sheet lookup across Workspace, CLI, and MCP

**What to build:** A user who renames a Sheet through Workspace or MCP can find and open the same Sheet under its new display name. The Sheet module owns Workspace membership and lookup by display name or Workspace-relative path. Saved placements use the path, and CLI and MCP retain the `Main` name for the root Sheet.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

**Spec requirements:** FLOW-06, DASH-03

- [ ] Rename a secondary Sheet in the Workspace UI. Verify the new name in the UI, CLI, and MCP. Open it under that name through CLI and MCP and confirm both resolve the original entrypoint.
- [ ] Rename the root Sheet through MCP. Confirm the new display name in the Workspace, and verify that CLI and MCP can still open the root Sheet as `Main`.
- [ ] Resolve the same Workspace-relative Sheet paths before and after rename, including `.` for the root Sheet. Reject paths outside the Workspace. Treat the display name and tab index as presentation and routing data.

This ticket covers display-name changes through Rename. Moving a Sheet folder or distinguishing a replacement Sheet created at the same path is outside its scope.
