# 19: Serve the web application and MCP over HTTPS

**What to build:** A developer can use the installed application from a remote browser and MCP client over HTTPS with ordinary certificate validation.

**Blocked by:** [09: Grant and revoke MCP Workspace access](09-grant-and-revoke-mcp-workspace-access.md), [16: Start Phyzflow at boot on Linux](16-start-phyzflow-at-boot-on-linux.md)

**Status:** ready-for-agent

**Spec requirements:** DEP-01, DEP-02, DEP-04

- [ ] Put Caddy in front of the Phyzflow backend bound to loopback. Document the public DNS and certificate setup and the private-hostname setup using Caddy's internal CA.
- [ ] From a separate client, verify HTTPS certificate and hostname validation, web administrator sign-in, and an allowed and denied MCP request. Verify that a client without trust in the private CA rejects that endpoint.
- [ ] Verify direct HTTP on an isolated test network and record the deployment configuration for release testing.

