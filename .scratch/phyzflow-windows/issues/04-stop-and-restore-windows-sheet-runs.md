# 04: Stop and restore Windows sheet runs

**What to build:** A Windows operator can stop a sheet and its child processes independently, recover from a worker crash, and resume interrupted work when the Phyzflow backend restarts.

**Blocked by:** [02: Create a Workspace and run a flow on Windows](02-create-a-workspace-and-run-a-flow-on-windows.md)

**Status:** ready-for-agent

- [ ] Run two sheets, with one launching a child process. Stop that sheet from the browser and verify that its worker and child exit, the other sheet continues, and the stopped sheet does not restart.
- [ ] Terminate a worker unexpectedly. Verify that its child processes exit and that the sheet starts a fresh run at its first step. Repeat until the existing retry limit is exhausted and verify that the sheet reports failure.
- [ ] Interrupt the backend while a sheet is active, then start the backend without opening the browser. Verify that no processes from the interrupted run survive and that recovery starts a new run at the first step. Verify that manually stopped and completed runs stay in their recorded states.
- [ ] Verify that the macOS stop, worker crash, and application restart tests still pass.

The Windows process lifecycle verified here is a prerequisite for the real reboot check in [Start Phyzflow at boot on Windows](../../application-spec/issues/18-start-phyzflow-at-boot-on-windows.md).
