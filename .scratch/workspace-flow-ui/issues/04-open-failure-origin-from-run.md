# 04: Open the failure origin from a run

**What to build:** Move straight from a run failure to the failed node and its project source. Inspect a skipped node to see the upstream dependency outcome that prevented it from running.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] A failed node's error offers an action that selects it and brings it into view on the graph.
- [x] When the run identifies an available project file and line within the node's source tree, the error can open that file with the failing line selected or visible. When source is unavailable, the diagnostic still shows the recorded location.
- [x] Skipped nodes name the failed or skipped dependencies that blocked them when known. Healthy branches retain their actual statuses.
- [x] A browser test triggers a nested Python failure and verifies navigation to the failed node and source and the reason a dependent node was skipped.

## Verification

A Chrome test verified that a nested project failure focuses the node and selects the failing line, that skipped nodes name their blockers, and that the independent branch succeeds. The existing run-history privacy test also passed with the installed SDK wheel.
