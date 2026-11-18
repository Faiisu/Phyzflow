# 10: Share sheet editing locks between MCP and the web

**What to build:** An authorized editor can edit a sheet exclusively while others follow it read-only, and MCP returns the sheet to an editable state after clean closure.

**Blocked by:** [09: Grant and revoke MCP Workspace access](09-grant-and-revoke-mcp-workspace-access.md)

**Status:** ready-for-agent

**Spec requirements:** EDIT-01, EDIT-02

- [ ] Open a sheet for editing through MCP. From another authorized session, view the sheet read-only and verify that writes through either the web or MCP are rejected. Verify that an unlocked sheet remains editable.
- [ ] Edit a sheet through the web and verify that a second MCP editing session cannot write to that sheet.
- [ ] Close an MCP editing session after success, failure, and cancellation. Verify that its operation-owned processes exit and the lock is released after closure.
- [ ] Hand off a flow run to the Workspace and close the editing session. Verify that the Workspace continues to manage the run.

