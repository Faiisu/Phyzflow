# 09: Grant and revoke MCP Workspace access

**What to build:** The administrator can issue an MCP token in the web application, restrict it to selected Workspaces, and revoke access.

**Blocked by:** [08: Sign in as the built-in administrator](08-sign-in-as-the-built-in-administrator.md)

**Status:** ready-for-agent

**Spec requirements:** AUTH-01, MCP-02, MCP-03

- [ ] Sign in and issue a token for one of two Workspaces. Use the MCP client to inspect the granted Workspace and verify that the deployed instance denies access to the other.
- [ ] Change or revoke the Workspace grant in the web application. Verify that the next request using the previous permission is denied by the deployed instance.
- [ ] Verify that an invalid token cannot access either Workspace.

