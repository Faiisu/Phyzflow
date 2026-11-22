# 03: Align Workspace Dashboard Panel file ownership

**What to build:** Workspace Dashboard Panel assets belong to the Workspace. Authorized users can manage them through the Dashboard Editor and the existing MCP file tools under Workspace access rules, with the same ownership whether the Workspace contains a Main Sheet or only secondary Sheets.

**Blocked by:** None (can start immediately).

**Status:** wontfix

- [ ] With an active edit lease on Main, a user with Workspace access can create, save, and remove a Workspace Dashboard Panel in the browser and edit its assets through MCP without a Main edit lease.
- [ ] A Workspace with only secondary Sheets supports the same browser and MCP edits. An edit lease on a secondary Sheet leaves Workspace panel edits available.
- [ ] Workspace Dashboard asset edits enforce existing Workspace authorization and file revision checks. The ownership rule applies only to Workspace Dashboard assets, and edits to files owned by a Sheet still respect that Sheet's edit lease.

## Comments

2026-11-21. The approved Workspace Dashboard design uses Sheet-owned panels. Workspace owns the placement and layout. The source Sheet owns the files. This ticket is superseded by [04: Add Sheet panels to the Workspace Dashboard](04-add-sheet-panels-to-workspace-dashboard.md).
