# 01: Create a Workspace and run its first flow

**What to build:** From the landing page, a developer can create and open a Workspace, then run a Python flow and see the result in that sheet's console.

**Blocked by:** None (can start immediately)

**Status:** resolved

**Spec requirements:** WS-01, WS-03, FLOW-01, FLOW-03, RUN-01

- [x] Create a Workspace from the landing page. Verify that it appears in the list, opens successfully, and has a Python 3.12 `.venv` provisioned with uv.
- [x] Run a one-time sample flow with named Python steps from the opened Workspace. Verify its expected result, terminal state, and distinguishable stdout and stderr in the run console.
- [x] Restart the application and verify that the Workspace remains listed and can be reopened and run.


## Verification

Browser acceptance passed on macOS 27.0.1 with CPython 3.12.11 and uv 0.7.12. The tests launch the installed `workspace app` command, create a Workspace from the landing page, and verify that uv provisioned Python 3.12 at the project root. They run a sample with two connected Python steps, verify stdout and child-process stderr in separate areas of the live console while the run is active, and check the succeeded state, expected result, and interpreter path. After restarting the application, they reopen the registered Workspace and run it again.

Run from the repository root with a Python environment that has the SDK and Playwright installed, with Google Chrome available.

```sh
PYTHONPATH=workspace-test python -m unittest discover -s workspace-test/tests -p test_application.py
```
