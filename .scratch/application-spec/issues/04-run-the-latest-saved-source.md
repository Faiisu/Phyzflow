# 04: Use saved source on the next run

**What to build:** A developer can save Python edits while a sheet runs and see the edits take effect on its next run.

**Blocked by:** [01: Create a Workspace and run its first flow](01-create-and-run-a-workspace.md)

**Status:** ready-for-agent

**Spec requirements:** FLOW-05

- [ ] Start a flow that pauses before completing, save an observable source change while it is active, and release the pause. Verify that the active run completes with its original behavior.
- [ ] Start the sheet again and verify that the saved change takes effect without a manual reload.
- [ ] Reuse the same acceptance case to check existing source reload behavior before making changes.

