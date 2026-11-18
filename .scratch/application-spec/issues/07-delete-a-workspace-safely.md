# 07: Delete a Workspace with running work

**What to build:** A developer can delete a Workspace from the landing page and find its folder in the host OS Trash or Recycle Bin after all its work has stopped.

**Blocked by:** [05: Run and stop sheets independently](05-run-all-and-stop-sheets-independently.md)

**Status:** ready-for-agent

**Spec requirements:** WS-02

- [ ] Delete a Workspace with multiple running sheets and an active child process. Verify that those processes exit before the folder is moved, stay stopped after deletion, and the entry leaves the landing page.
- [ ] Exercise a stop attempt that leaves a process running. Verify that deletion fails, the folder and entry remain, and the UI directs the user to resolve the process through the host OS before retrying.
- [ ] Exercise an OS rejection of the move to Trash. Verify that the UI reports the failure and the Workspace remains registered.

