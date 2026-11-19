# 03: Use MCP with a Windows Workspace

**What to build:** An MCP client can validate a flow on an installed Windows backend using the granted Workspace's Python environment. The application enforces Workspace permissions and protects its own source checkout across launch directories.

**Blocked by:** [02: Create a Workspace and run a flow on Windows](02-create-a-workspace-and-run-a-flow-on-windows.md)

**Status:** ready-for-agent

- [ ] Issue a scoped token from the web application and use it from a separate MCP client. Verify that flow validation runs with the granted Workspace's Python environment and returns the correct stage and source location for an invalid flow.
- [ ] Verify that the token cannot access an ungranted Workspace. Revoke access to the granted Workspace and verify that the client's next request is denied.
- [ ] Start the installed app from a working directory outside the Phyzflow checkout. Verify that the checkout and its children are rejected when adding a Workspace and remain outside the MCP editing scope.
