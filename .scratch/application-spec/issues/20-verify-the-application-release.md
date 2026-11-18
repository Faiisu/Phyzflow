# 20: Verify acceptance before releasing a version

**What to build:** An operator or maintainer can collect evidence that a new feature and all agreed application behavior pass on installed Phyzflow deployments before release.

**Blocked by:** [02: Add an existing project as a Workspace](02-add-an-existing-workspace.md), [07: Delete a Workspace with running work](07-delete-a-workspace-safely.md), [11: Recover a stuck sheet with Force Open](11-force-open-a-stale-sheet-session.md), [13: Complete the MCP flow-development commands](13-complete-mcp-flow-development-commands.md), [17: Start Phyzflow at boot on macOS](17-start-phyzflow-at-boot-on-macos.md), [18: Start Phyzflow at boot on Windows](18-start-phyzflow-at-boot-on-windows.md), [19: Serve the web application and MCP over HTTPS](19-serve-the-web-and-mcp-over-https.md)

**Status:** ready-for-agent

**Spec requirements:** REL-01, REL-02, DEP-01, DEP-02, DEP-03, DEP-04

- [ ] Use fresh projects created for the target version. Record the new feature, build, exact OS versions, installed hosts or VMs, remote clients, HTTPS hostnames, execution accounts, and board or peripheral used.
- [ ] Run the application acceptance scenarios from a separate client against installed macOS, Linux, and Windows backends. Exercise on-premises and cloud setups, isolated HTTP and HTTPS, real host reboots, and normal browser and MCP certificate validation.
- [ ] On a supported host with a connected board or peripheral, verify that a flow can perform a real device operation as the configured execution account.
- [ ] Record observed results and evidence by requirement. Fix any failures and repeat the affected scenarios. Release acceptance passes when every agreed feature scenario has passed.

