# Flow page loading reliability

This work addresses the four findings from the Flow page review at `b63ac01`. The Workspace opens a Sheet Worker and embeds its revisioned Flow View. The frontend loads `/project` to build and render the graph. With invalid Python, it displays the last loaded graph or a source preview.

The acceptance criteria follow [FLOW-01 and FLOW-05](../../docs/application-spec.md) and the [first-release Web UI ticket](../workspace-first-release/issues/03-run-and-debug-the-sample-in-the-web-ui.md). The work preserves the existing Python graph and run lifecycle, saved console behavior, and dashboard ownership.

| Issue | Affected behavior |
| --- | --- |
| [01](issues/01-keep-recovery-graph-and-node-files-in-sync.md) | A stale source preview can open another node's files. |
| [02](issues/02-load-the-graph-independently-of-dashboard-and-console.md) | Dashboard or console I/O can take the flow editor offline. |
| [03](issues/03-render-large-graph-edges-on-high-dpi-displays.md) | Large graphs lose their edges on high-DPI displays. |
| [04](issues/04-open-only-the-linked-sheet.md) | An initial sheet link opens a second worker and iframe. |
