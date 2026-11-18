# 07: Delete a Workspace with running work

**What to build:** A developer can delete a Workspace from the landing page and find its folder in the host OS Trash or Recycle Bin after all its work has stopped.

**Blocked by:** [05: Run and stop sheets independently](05-run-all-and-stop-sheets-independently.md)

**Status:** resolved

**Spec requirements:** WS-02

- [x] Delete a Workspace with multiple running sheets and an active child process. Verify that those processes exit before the folder is moved, stay stopped after deletion, and the entry leaves the landing page.
- [x] Exercise a stop attempt that leaves a process running. Verify that deletion fails, the folder and entry remain, and the UI directs the user to resolve the process through the host OS before retrying.
- [x] Exercise an OS rejection of the move to Trash. Verify that the UI reports the failure and the Workspace remains registered.

## Verification

Browser acceptance passed on macOS 27.0.1 with CPython 3.12.11, uv 0.7.12, and Google Chrome. All 19 tests in `test_application*.py` passed against the installed `workspace app` entry point, including all four Workspace deletion tests in `workspace-test/tests/test_application_delete_workspace.py`.

- Deleting a Workspace with two running sheets and active child processes (including a `SIGTERM`-ignoring child and a slow-exiting child) stops all sheet and child processes and verifies they exit before moving the folder to macOS `~/.Trash`, keeps them stopped, and removes the Workspace from the landing page.
- Pausing a running supervisor process (`SIGSTOP`) causes the stop attempt to time out, fences concurrent HTTP and MCP access while deletion is active, leaves the folder and landing-page entry in place, directs the user to resolve the active process through the operating system before retrying Delete, keeps stopped sibling sheets stopped, and succeeds on retry after resuming and exiting that process.
- Spawning a detached `.venv` child process (`start_new_session=True`) is detected after stopping sheet workers and even on an unmounted Workspace after restarting `workspace app`, blocking deletion until the host process exits.
- Rejecting the Trash move (`0o500` `PHYZFLOW_TRASH_DIR`) reports the failure in the landing-page alert, keeps the Workspace registered and on disk across reload, and succeeds once Trash permissions allow the move (including collision suffixing in Trash).

Run from `workspace-test` with the SDK and Playwright installed:

```sh
/tmp/phyzflow-app-07-venv/bin/python -m unittest discover -s tests -p 'test_application*.py' -v
```

