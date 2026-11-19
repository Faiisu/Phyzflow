# 05: Delete a Workspace to the Windows Recycle Bin

**What to build:** A Windows operator can delete a Workspace from the landing page and find its folder in the Windows Recycle Bin after the application verifies that its processes have stopped.

**Blocked by:** [04: Stop and restore Windows sheet runs](04-stop-and-restore-windows-sheet-runs.md)

**Status:** ready-for-agent

- [ ] Delete a Workspace with an active sheet and child process from the browser. Verify that both processes exit before the folder moves, remain stopped, and the Workspace entry is removed after the folder reaches the Windows Recycle Bin.
- [ ] Exercise a stop attempt that leaves a Workspace process active. Verify that deletion reports the active process and retains the folder and its registration.
- [ ] Exercise an OS rejection of the move to the Recycle Bin. Verify that the UI reports the failure, retains the folder and its registration, and lets the operator reopen the Workspace.
- [ ] Verify the Recycle Bin behavior under the Windows deployment account in a headless session.
