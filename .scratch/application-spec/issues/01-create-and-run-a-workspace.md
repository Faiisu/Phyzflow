# 01: Create a Workspace and run its first flow

**What to build:** From the landing page, a developer can create and open a Workspace, then run a Python flow and see the result in that sheet's console.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Spec requirements:** WS-01, WS-03, FLOW-01, FLOW-03, RUN-01

- [ ] Create a Workspace from the landing page. Verify that it appears in the list, opens successfully, and has a Python 3.12 `.venv` provisioned with uv.
- [ ] Run a one-time sample flow with named Python steps from the opened Workspace. Verify its expected result, terminal state, and distinguishable stdout and stderr in the run console.
- [ ] Restart the application and verify that the Workspace remains listed and can be reopened and run.

