# 14: Restart unexpectedly terminated runs up to three times

**What to build:** A developer can leave a flow running and have Phyzflow restart it from the first step after an unexpected execution-process exit.

**Blocked by:** [05: Run and stop sheets independently](05-run-all-and-stop-sheets-independently.md)

**Status:** resolved

**Spec requirements:** RUN-05

- [x] Terminate a sheet's execution process while the host and application stay up. Verify that the sheet starts a new run at the first step and other sheets continue.
- [x] Cause the original run and all three restart attempts to terminate unexpectedly. Verify three automatic retries, a visible Failed state, and no fourth retry until an explicit Run.
- [x] Verify that an explicit Stop and a flow-reported terminal result do not trigger the crash-restart rule.

## Verification

Browser acceptance passed on macOS 27.0.1 (26A434) with CPython 3.12.11 and Google Chrome 157.0.8103.79. The test signs in to the installed `workspace app` and sends SIGKILL to the execution worker of each active attempt. The first step records four distinct attempts with the same explicit input. A concurrent sheet continues with its original run ID and produces additional units after every kill. Main shows Failed after the three automatic retries and starts again only after an explicit run. A flow-reported failure and an explicit Stop produce no replacement attempts.

The full installed application suite passed all 14 tests. Run from the repository root with the SDK's MCP extra and the test project's verification extra installed, and with Google Chrome available.

```sh
PYTHONPATH=workspace-test python -m unittest discover -s workspace-test/tests -p 'test_application*.py' -v
```

