# 05: Delete a Workspace to the Windows Recycle Bin

**What to build:** A Windows operator can delete a Workspace from the landing page and find its folder in the Windows Recycle Bin after the application verifies that its processes have stopped.

**Blocked by:** [04: Stop and restore Windows sheet runs](04-stop-and-restore-windows-sheet-runs.md)

**Status:** resolved

- [x] Delete a Workspace with an active sheet and child process from the browser. Verify that both processes exit before the folder moves, remain stopped, and the Workspace entry is removed after the folder reaches the Windows Recycle Bin.
- [x] Exercise a stop attempt that leaves a Workspace process active. Verify that deletion reports the active process and retains the folder and its registration.
- [x] Exercise an OS rejection of the move to the Recycle Bin. Verify that the UI reports the failure, retains the folder and its registration, and lets the operator reopen the Workspace.
- [x] Verify the Recycle Bin behavior under the Windows deployment account in a headless session.

## Verification

Browser acceptance passed on the Windows 11 IoT Enterprise build `26200.9974` deployment account in an SSH session with no interactive desktop. A running worker and child stopped before the project appeared in the native Recycle Bin. A detached process referenced by a forward-slash Windows path blocked deletion. A sharing violation preserved the project and its registration, and the project reopened from the landing page. A disposable SUBST drive reproduced a permanently removed fixture on the earlier code. The native recycle-only path in `4814aa3` refused the same request and retained the fixture and registration. Four macOS deletion regression tests passed.
