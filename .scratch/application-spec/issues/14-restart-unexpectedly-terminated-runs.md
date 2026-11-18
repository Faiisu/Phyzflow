# 14: Restart unexpectedly terminated runs up to three times

**What to build:** A developer can leave a flow running and have Phyzflow restart it from the first step after an unexpected execution-process exit.

**Blocked by:** [05: Run and stop sheets independently](05-run-all-and-stop-sheets-independently.md)

**Status:** ready-for-agent

**Spec requirements:** RUN-05

- [ ] Terminate a sheet's execution process while the host and application stay up. Verify that the sheet starts a new run at the first step and other sheets continue.
- [ ] Cause the original run and all three restart attempts to terminate unexpectedly. Verify three automatic retries, a visible Failed state, and no fourth retry until an explicit Run.
- [ ] Verify that an explicit Stop and a flow-reported terminal result do not trigger the crash-restart rule.

