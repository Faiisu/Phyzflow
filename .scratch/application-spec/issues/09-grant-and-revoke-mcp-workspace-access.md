# 09: Grant and revoke MCP Workspace access

**What to build:** The administrator can issue an MCP token in the web application, restrict it to selected Workspaces, and revoke access.

**Blocked by:** [08: Sign in as the built-in administrator](08-sign-in-as-the-built-in-administrator.md)

**Status:** resolved

**Spec requirements:** AUTH-01, MCP-02, MCP-03

- [x] Sign in and issue a token for one of two Workspaces. Use the MCP client to inspect the granted Workspace and verify that the deployed instance denies access to the other.
- [x] Change or revoke the Workspace grant in the web application. Verify that the next request using the previous permission is denied by the deployed instance.
- [x] Verify that an invalid token cannot access either Workspace.


## Verification

On macOS 27.0.1 with CPython 3.12.11, Chrome and the MCP Python SDK 2.3.0 exercised the installed `workspace app` on loopback. The browser issued a token for one of two registered Workspaces and showed the token value only at issuance. The MCP client listed and inspected the granted Workspace and received an access-denied tool result for the other. Saving different grants changed the next request from the same client. Revoking the token caused its next command to fail authentication. A correctly shaped unissued bearer received HTTP 401 for inspection requests targeting either Workspace. The browser session could not authenticate to MCP, and an MCP bearer could not authenticate to the web routes. Token management required a signed-in browser and a same-origin mutation.

The test failed before implementation because the landing page lacked MCP access management. To repeat it from the repository root with Chrome installed:

```sh
uv pip install './workspace[mcp]' 'playwright==1.65.2'
PYTHONPATH=workspace-test python -m unittest discover -s workspace-test/tests -p test_application_mcp.py -v
```

Remote HTTPS verification is tracked by [ticket 19](19-serve-the-web-and-mcp-over-https.md).
