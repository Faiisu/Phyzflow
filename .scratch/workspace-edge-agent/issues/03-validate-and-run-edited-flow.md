# 03: Validate and run an edited flow through the agent

**What to build:** A client edits a flow, validates it, starts it as an agent-owned job, and reads its output or failure diagnostics while the Workspace UI can inspect the same completed run.

**Blocked by:** 02: Create and edit flow source through the agent.

**Status:** ready-for-agent

- [ ] Validation uses the selected project interpreter and shared project checks; a nested syntax or import failure identifies its stage and location before node execution.
- [ ] Starting a flow returns a job identifier; clients can inspect queued, running, succeeded, failed, or cancelled state and request cancellation.
- [ ] Successful runs expose their output and source version; failed runs identify the node and source location, including downstream nodes skipped because of that failure.
- [ ] The agent and UI coordinate execution for the same sheet and share completed flow history; a conflicting start is reported clearly.
- [ ] Integration coverage edits and runs a successful flow, diagnoses a nested failure, and confirms that the UI shows matching completed run information.
- [ ] Keep request values and request-derived exception messages out of stored flow history by default; explicit development diagnostics retain access controls.
