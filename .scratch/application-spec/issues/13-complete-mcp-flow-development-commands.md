# 13: Complete the MCP flow-development commands

**What to build:** An authorized MCP client can complete the same flow-development work available in the web application within its granted Workspace.

**Blocked by:** [04: Use saved source on the next run](04-run-the-latest-saved-source.md), [05: Run and stop sheets independently](05-run-all-and-stop-sheets-independently.md), [06: Save and expire console logs per sheet](06-save-and-expire-sheet-console-logs.md), [12: Author a themed dashboard with AI](12-author-a-themed-dashboard-with-ai.md)

**Status:** ready-for-agent

**Spec requirements:** MCP-01, MCP-02, MCP-03

- [ ] Compare the flow-development commands available in the web app with the MCP command list. Exercise source and dashboard work, validation, running and stopping, inspecting execution and logs, and installing a test dependency into the shared Workspace environment through MCP.
- [ ] Verify that saved code and installed dependencies affect the expected sheet runs and that the deployed instance enforces Workspace permissions for each operation.
- [ ] Use representative denied operations to verify that the same token cannot change the Phyzflow application's source or specification or update or restart the deployed application itself.
- [ ] Record evidence through an actual MCP client, including successful and denied operations.

