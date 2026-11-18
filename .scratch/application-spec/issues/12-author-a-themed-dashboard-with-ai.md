# 12: Author a themed dashboard with AI

**What to build:** An AI agent can author a dashboard panel for a Workspace flow, and a developer can open it to see real run data in the Phyzflow theme.

**Blocked by:** [03: Inspect a flow step by step](03-inspect-named-flow-steps.md), [10: Share sheet editing locks between MCP and the web](10-share-sheet-editing-locks-with-mcp.md)

**Status:** ready-for-agent

**Spec requirements:** DASH-01, DASH-02

- [ ] Have an AI agent write panel assets using the Live Counter example, the shared base stylesheet, and the existing dashboard subscription bridge.
- [ ] Open the panel in the Workspace. Run a flow with known metrics and verify that the displayed values follow the actual run snapshot in the existing panel sandbox.
- [ ] Review the panel alongside the host UI in idle, live, error, and narrow-panel states. Verify that typography, colors, spacing, chart labels, and interaction states follow the shared theme.

