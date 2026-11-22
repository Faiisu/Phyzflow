# 02: Share Sheet edit leases for Workflow and Node Dashboard edits

**What to build:** A Sheet editor can create, save, or remove a Workflow or Node Dashboard Panel under the same Sheet edit lease used for other source edits. Other authorized users can inspect the panel and its source while the lease is held.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] An active MCP edit lease on a Sheet leaves Workflow and Node panels readable in the browser and rejects Dashboard Editor mutations from a different session.
- [x] A browser editing session can create, save, and remove its Sheet's panels. During that session, a different MCP session cannot mutate the panel files. A different unlocked Sheet remains independently editable.
- [x] An authorized save with the current file revision updates the preview. An outdated revision is rejected and the newer content remains on disk.

## Verification

Browser and MCP lease tests verified read-only dashboard inspection during an active MCP edit lease, exclusive browser Dashboard Editor leases that block conflicting MCP mutations while leaving other sheets editable, and revision-checked saves.
