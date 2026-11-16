# 08: Recover local run history across browser disconnects

**What to build:** Let a developer leave the local Web UI while a run is in progress and return later to browse the results with source version and diagnostic context.

**Blocked by:** 07 (Set performance limits for the first workload)

**Status:** ready-for-agent

- [ ] A run completes after the browser disconnects. Reconnecting shows its terminal status, duration, node results and summaries, and available failure locations.
- [ ] After editing source without committing and reopening the UI, history distinguishes runs by the source version actually loaded and retains the source locations recorded for each run.
- [ ] Stored summaries include applicable metadata such as tensor shape, dtype, and device.
- [ ] A request and a thrown exception containing the same test value leave no copy of that value in the persisted default diagnostics. The exception type and source location remain useful.
