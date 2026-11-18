# 05: Run and stop sheets independently

**What to build:** A developer can run one sheet or start every idle sheet with Run All and manage each run independently.

**Blocked by:** [01: Create a Workspace and run its first flow](01-create-and-run-a-workspace.md)

**Status:** ready-for-agent

**Spec requirements:** RUN-01, RUN-02, RUN-04, FLOW-03

- [ ] Complete a one-time flow, trigger an automation whose waiting logic is implemented in Python, and observe a continuous flow across multiple units of work.
- [ ] Start one sheet and record its run ID. Select Run All and verify that the active run keeps its ID and each idle sheet starts concurrently using the Workspace environment.
- [ ] Cause one sheet to fail to start, then cause a running sheet to raise an error. Verify that other sheets start or continue and that consoles show only their own run output.
- [ ] Stop one active sheet and verify that its work ends while a different sheet continues. Stop the Workspace process and verify that its remaining work stops.

