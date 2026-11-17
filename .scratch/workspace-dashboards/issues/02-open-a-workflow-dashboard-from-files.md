# 02: Open a workflow dashboard from files

Status: resolved

**What to build:** Open Visualize on the workflow to find bottlenecks across nodes and drill into a node dashboard.

**Blocked by:** 01: Open a node dashboard from files.

- [x] The overview renders separate project dashboard files and receives the workflow dependency graph and saved run status and timings.
- [x] A node selected from the overview opens its node dashboard for the same run.
- [x] Telemetry Control Room demonstrates timing comparison and drill-down, including a run with a failed node.

## Verification

Chrome opened the Telemetry Control Room overview and showed durations, the failed pipeline, and skipped nodes from a forced scoring failure. Opening the pipeline dashboard kept the same run selected. A successful overview was also inspected at desktop width.
