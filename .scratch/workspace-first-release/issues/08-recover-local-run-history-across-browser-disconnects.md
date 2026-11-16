# 08: Recover local run history across browser disconnects

**What to build:** Let a developer leave the local Web UI while a run is in progress and return later to browse the results with source version and diagnostic context.

**Blocked by:** 07 (Set performance limits for the first workload)

**Status:** resolved

- [x] A run completes after the browser disconnects. Reconnecting shows its terminal status, duration, node results and summaries, and available failure locations.
- [x] After editing source without committing and reopening the UI, history distinguishes runs by the source version actually loaded and retains the source locations recorded for each run.
- [x] Stored summaries include applicable metadata such as tensor shape, dtype, and device.
- [x] A request and a thrown exception containing the same test value leave no copy of that value in the persisted default diagnostics. The exception type and source location remain useful.

## Verification

The browser test closed the page while Predict was running, then reopened it and recovered the failure from [Run history](../../../workspace-test/README.md#recover-a-completed-run). It checked tensor metadata and locations from a chained exception and confirmed that a value used as request data, an exception message, a dynamic exception name, and a synthetic stack filename was absent from the saved files. The test edited the source while the first UI was running, restarted the UI, then verified that the new run had a different source version while the old run retained its original locations.

All six Workspace acceptance tests, both Linux ARM64 Docker integration tests, and the Python and JavaScript checks passed. On the final implementation, three rounds of the [trained workload benchmark](../../../workspace-test/deployment-trial/README.md#benchmark-the-trained-workload) stayed within Ticket 07's limits. The maximum paired p95 and peak RSS increases were 0.034 ms and 0.235 MiB for the Runner, and 1.947 ms and 0.734 MiB for the live UI.
