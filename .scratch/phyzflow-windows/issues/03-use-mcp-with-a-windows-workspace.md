# 03: Use MCP with a Windows Workspace

**What to build:** An MCP client can validate a flow on an installed Windows backend using the granted Workspace's Python environment. The application enforces Workspace permissions and protects its own source checkout across launch directories.

**Blocked by:** [02: Create a Workspace and run a flow on Windows](02-create-a-workspace-and-run-a-flow-on-windows.md)

**Status:** resolved

- [x] Issue a scoped token from the web application and use it from a separate MCP client. Verify that flow validation runs with the granted Workspace's Python environment and returns the correct stage and source location for an invalid flow.
- [x] Verify that the token cannot access an ungranted Workspace. Revoke access to the granted Workspace and verify that the client's next request is denied.
- [x] Start the installed app from a working directory outside the Phyzflow checkout. Verify that the checkout and its children are rejected when adding a Workspace and remain outside the MCP editing scope.

## Verification

Commit `af09259` passed remote browser and MCP acceptance on Windows 11 IoT Enterprise build `26200.9974`. A scoped client validated in the Workspace interpreter, received the validation stage and source line for invalid Python, and was denied access to ungranted or revoked Workspaces. The app protected its source checkout when started from another directory, including a checkout path containing spaces. MCP rejected reads and writes through a junction into the checkout. Three related macOS browser and MCP tests passed.
