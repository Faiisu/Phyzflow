# Phyzflow application specification

The [application specification](../../docs/application-spec.md) defines the product requirements. This directory tracks the approved implementation tickets and release acceptance work.

For each ticket, start with its acceptance scenarios. Check the existing behavior through the stated entry point, implement any missing behavior, and repeat the scenarios. The release ticket verifies the full specification on installed hosts.

## Tickets

1. [Create a Workspace and run its first flow](issues/01-create-and-run-a-workspace.md)
2. [Add an existing project as a Workspace](issues/02-add-an-existing-workspace.md) (blocked by 01)
3. [Inspect a flow step by step](issues/03-inspect-named-flow-steps.md) (blocked by 01)
4. [Use saved source on the next run](issues/04-run-the-latest-saved-source.md) (blocked by 01)
5. [Run and stop sheets independently](issues/05-run-all-and-stop-sheets-independently.md) (blocked by 01)
6. [Save and expire console logs per sheet](issues/06-save-and-expire-sheet-console-logs.md) (blocked by 01)
7. [Delete a Workspace with running work](issues/07-delete-a-workspace-safely.md) (blocked by 05)
8. [Sign in as the built-in administrator](issues/08-sign-in-as-the-built-in-administrator.md) (blocked by 01)
9. [Grant and revoke MCP Workspace access](issues/09-grant-and-revoke-mcp-workspace-access.md) (blocked by 08)
10. [Share sheet editing locks between MCP and the web](issues/10-share-sheet-editing-locks-with-mcp.md) (blocked by 09)
11. [Recover a stuck sheet with Force Open](issues/11-force-open-a-stale-sheet-session.md) (blocked by 10)
12. [Author a themed dashboard with AI](issues/12-author-a-themed-dashboard-with-ai.md) (blocked by 03, 10)
13. [Complete the MCP flow-development commands](issues/13-complete-mcp-flow-development-commands.md) (blocked by 04, 05, 06, 12)
14. [Restart unexpectedly terminated runs up to three times](issues/14-restart-unexpectedly-terminated-runs.md) (blocked by 05)
15. [Restore the latest run state after restart](issues/15-restore-work-after-phyzflow-restarts.md) (blocked by 14)
16. [Start Phyzflow at boot on Linux](issues/16-start-phyzflow-at-boot-on-linux.md) (blocked by 15)
17. [Start Phyzflow at boot on macOS](issues/17-start-phyzflow-at-boot-on-macos.md) (blocked by 15)
18. [Start Phyzflow at boot on Windows](issues/18-start-phyzflow-at-boot-on-windows.md) (blocked by 15)
19. [Serve the web application and MCP over HTTPS](issues/19-serve-the-web-and-mcp-over-https.md) (blocked by 09, 16)
20. [Verify acceptance before releasing a version](issues/20-verify-the-application-release.md) (blocked by 02, 07, 11, 13, 17, 18, 19)

