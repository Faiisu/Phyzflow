# 02: Inspect node execution timing

**What to build:** Use an Execution tab in the Node inspector to examine the selected node's status, start time, finish time, and duration for the current run or a run selected in history.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] The Execution tab identifies the run it describes. Node timing in the current run updates from Runner events as the node starts and finishes.
- [x] Selecting a history entry shows timing for that run and does not combine it with values from a newer run. Nodes skipped by the Runner show their status without an invented execution interval.
- [x] Older records with missing timestamps and selected nodes absent from a historical graph show which timing is unavailable.
- [x] A browser test verifies that Reconcile Events and Aggregate Alerts show their actual execution order even when the graph places them in the same column.

## Verification

A Chrome test following a Telemetry run verified live timing, actual sequential execution of adjacent branches, saved run selection, missing legacy timing, and skipped nodes. The test passed with the installed SDK wheel.
