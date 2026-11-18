# 11: Recover a stuck sheet with Force Open

**What to build:** An administrator can inspect a possibly stale MCP editing session and decide whether to reopen the sheet from its latest saved source.

**Blocked by:** [10: Share sheet editing locks between MCP and the web](10-share-sheet-editing-locks-with-mcp.md)

**Status:** ready-for-agent

**Spec requirements:** EDIT-03

- [ ] Keep an idle MCP session connected and sending heartbeats every 15 seconds. Verify that it keeps its lock. Stop heartbeats for 45 seconds or simulate an unclean disconnect and verify that the UI marks the session possibly stale.
- [ ] Verify the UI shows the lock owner and last heartbeat and explains what happens to unsaved changes. Verify that the lock remains in place until the user decides.
- [ ] Save source, leave a later change unsaved, and force open the stuck sheet. Verify that editing processes are cleaned up, the last saved source loads, and the old session cannot write after reconnecting.

