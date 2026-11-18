# 05: Run and stop sheets independently

**What to build:** A developer can run one sheet or start every idle sheet with Run All and manage each run independently.

**Blocked by:** [01: Create a Workspace and run its first flow](01-create-and-run-a-workspace.md)

**Status:** resolved

**Spec requirements:** RUN-01, RUN-02, RUN-04, FLOW-03

- [x] Complete a one-time flow, trigger an automation whose waiting logic is implemented in Python, and observe a continuous flow across multiple units of work.
- [x] Start one sheet and record its run ID. Select Run All and verify that the active run keeps its ID and each idle sheet starts concurrently using the Workspace environment.
- [x] Cause one sheet to fail to start, then cause a running sheet to raise an error. Verify that other sheets start or continue and that consoles show only their own run output.
- [x] Stop one active sheet and verify that its work ends while a different sheet continues. Stop the Workspace process and verify that its remaining work stops.



## Verification

Browser acceptance passed on macOS 27.0.1 with CPython 3.12.11, uv 0.7.12, and Google Chrome. The tests use the installed `workspace app` command and the administrator session. They cover the following behavior.

- Run All preserves the active automation's run ID and rejects a second explicit run of the same sheet. An unopened sheet uses its `sample.json` input and completes. The next Run All displays the result from the updated sample. The waiting automation activates after the browser disconnects, and its original run result is visible after reconnecting.
- Run All runs two continuous sheets concurrently for multiple units of work while a separate sheet reports a syntax error and another raises a Python exception after starting. The two continuous sheets import their Workspace-only dependency and report the root `.venv` interpreter. Each sheet's console shows its own standard output and standard error.
- Stopping one sheet ends its subprocess even when it ignores SIGTERM. The other sheet continues processing. Shutting down Phyzflow stops that sheet and its subprocess.

Run from the repository root with the SDK and Playwright installed.

```sh
PYTHONPATH=workspace-test python -m unittest discover -s workspace-test/tests -p 'test_application*.py'
```
