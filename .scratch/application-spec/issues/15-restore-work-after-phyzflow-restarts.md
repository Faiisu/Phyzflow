# 15: Restore the latest run state after restart

**What to build:** When Phyzflow starts again, it restores each sheet's last operating state and restarts interrupted runs from the first step.

**Blocked by:** [14: Restart unexpectedly terminated runs up to three times](14-restart-unexpectedly-terminated-runs.md)

**Status:** resolved

**Spec requirements:** RUN-03

- [x] Restart the installed backend with active one-time, automation, and continuous runs. Verify that each gets a new run and begins at its first step without a new command.
- [x] Include an automation actively waiting inside Python. Verify that its new run follows the flow's own waiting logic.
- [x] Include manually stopped and terminal runs. Verify that stopped work stays stopped and terminal runs are not replayed. Verify that shutdown of an active process does not record an explicit user Stop.

## Verification

Acceptance passed on macOS 27.0.1 (26A434) with CPython 3.12.11, uv 0.7.12, and Google Chrome 157.0.8103.79. All 24 tests in `test_application*.py` passed against the installed `workspace app` entry point, including all five restoration tests in `workspace-test/tests/test_application_restore_runs.py`.

- Stopping `workspace app` via `SIGTERM` or `SIGKILL` while one-time (paused in step 2 after starting with explicit custom input), Python-waiting automation, and continuous sheets are active, then starting `workspace app` without opening any browser page, restores all three automatically from step 1 with new run IDs and processes; the one-time flow keeps its custom input, the automation stays in Python waiting until its trigger file appears, and UI reconnection displays live output and status.
- Stopping one sheet explicitly with Stop, completing a sheet to `Succeeded`, and failing sheets via step exception and startup syntax error while keeping another sheet active until `SIGINT` shutdown preserves `Stopped`, `Succeeded`, and `Failed` without replay after restart, preserves their terminal UI tab states when selected, and restarts only the shutdown-interrupted active run.
- `RunStateStore` stores `run_state.sqlite3` with `0o600` permissions on POSIX, reconciles `RunHistory` terminal records during the acknowledgment crash window without replaying finished runs, cleans deleted sheet entries, and clears Workspace state entries on Workspace deletion.
- JavaScript syntax checks (`node --check`) and `git diff --check codex/phyzflow-application` passed, and regression checks passed across `test_auto_reload.py` (4/4), `test_agent_persistence.py` (7/7), `test_runner.py` (5/5), and `test_validation.py` (2/2).

Run from `workspace-test` with the SDK (`[mcp]`) and verification extras installed:

```sh
/tmp/phyzflow-app-15-venv/bin/python -m unittest discover -s tests -p 'test_application*.py' -v
```

