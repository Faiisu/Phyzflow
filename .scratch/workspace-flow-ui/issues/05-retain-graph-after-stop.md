# 05: Retain the execution graph after stopping a sheet

**What to build:** Stop an active sheet and continue inspecting the execution graph and the statuses last received for that run. Reopen the sheet explicitly when it is time to run again.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] Stop still terminates the requested sheet's worker. The graph, selected node, zoom, and last received run statuses stay visible, with a visible Stopped state that identifies the view as a snapshot.
- [x] Switching away and back within the current Workspace page retains the stopped snapshot. Reopening the stopped sheet resumes the normal ready and run controls.
- [x] A browser test stops a long run from its sheet tab, inspects the retained graph, and verifies that a second sheet continues to completion.

## Verification

The multi-sheet Chrome test stopped the worker and verified the retained selection, zoom, and node statuses across a tab switch while a second sheet completed. Reopening created a fresh ready view. The test passed with the installed SDK wheel.
