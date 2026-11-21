# 01: Align Dashboard Panel discovery and loading

**What to build:** A user who opens a Workflow or Node Dashboard Panel in the Flow Editor sees the same source panel that MCP identifies. Expose the same Sheet panel catalog for the Workspace Panels drawer to select from. Use one Dashboard Panel asset module to apply the same scope and Panel ID rules during discovery, preview, and editing.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] Across Workflow and Node scopes, MCP inspection and the browser agree on the identity and assets of each source panel that can be opened. A panel missing its entry document remains unavailable in the browser.
- [ ] Discovery identifies a panel by its source Sheet, scope, and Panel ID. Panels with the same ID in different sheets or scopes resolve to their own assets.
- [ ] A scope's Overview has the reserved ID `default`. A child panel with that ID never appears as a second Overview or opens the wrong assets.
- [ ] Confirming removal of an Overview in its source Sheet closes it and removes its assets. Its additional panels and unrelated files remain available.
