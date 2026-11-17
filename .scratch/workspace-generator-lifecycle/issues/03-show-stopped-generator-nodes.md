# 03: Show stopped generator nodes in the run snapshot

**What to build:** Stop a running generator flow from its sheet tab and see which nodes were still running when the sheet stopped. Keep the graph and most recent Live output available in the stopped view.

**Blocked by:** 01 (Track generator node completion).

**Status:** resolved

- [x] After Stop succeeds, each node that was Running in the retained view shows Stopped. Nodes that had already completed retain their recorded outcome.
- [x] Live output retains its latest value, stops updating, and stays visible when switching away and back within the current Workspace page.
- [x] A browser test stops Live Counter while both nodes are Running, verifies the stopped snapshot, and reopens the sheet to confirm that it can run again from the start.

## Verification

Chrome verified both active nodes change to Stopped, an already successful node retains its status, and the last output survives switching sheets. The stopped route returned 503, and reopening and running the sheet began at one.
