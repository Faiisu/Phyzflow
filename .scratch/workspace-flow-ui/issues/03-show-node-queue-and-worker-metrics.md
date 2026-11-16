# 03: Show queue and worker metrics in node execution details

**What to build:** Inspect measurements reported by a node that runs its own parallel pipeline. From the Execution tab, see which queue accumulated the most waiting time and how many workers were active together for the selected completed run.

**Blocked by:** 02 (Inspect node execution timing).

**Status:** resolved

- [x] In Telemetry Control Room, Run Parallel Pipeline shows its ingress queue, I/O workers, scoring queue, and CPU workers in execution order, with the relevant queue capacity, maximum depth, blocked enqueue count, p95 wait, and recorded I/O and CPU overlap.
- [x] The UI identifies the measurements as a snapshot from the selected completed run. Changing runs updates the values, and a node without measurements has no fabricated queue or worker values.
- [x] Flows can opt in to bounded operational metrics without capturing raw inputs or outputs. Reporting remains compatible with ordinary Python nodes and older run history.
- [x] Verify in the browser that the queue with the longest p95 wait can be identified from the Inspector and agrees with the Telemetry run report.

## Verification

Chrome verified stage order, queue and worker values against the Telemetry run output, selection of a previous run with a different queue capacity, and the absence of metrics on a node that did not report them. Runner tests verified bounded numeric collection without input or output samples and preserved metrics reported before a node failed. The complete suite passed with the installed SDK wheel (25 tests), and Pyright reported no errors.
