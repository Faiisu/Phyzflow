# 06: Save and expire console logs per sheet

**What to build:** A developer can view live console output and configure saved logs independently for each sheet.

**Blocked by:** [01: Create a Workspace and run its first flow](01-create-and-run-a-workspace.md)

**Status:** resolved

**Spec requirements:** FLOW-03, FLOW-04

- [x] Run a sheet with default settings and verify that stdout and stderr are saved by run under the sheet's log directory and remain available after reopening. Verify that saving is enabled and retention defaults to seven days.
- [x] Turn saving off for one sheet, run it again, and verify that it produces live console output without creating saved logs for that run. Verify that another sheet retains its original settings.
- [x] Change retention for a sheet and verify daily log rotation and expiry across a date boundary during a continuous run. Verify that cleanup preserves unexpired logs.

## Verification

Browser acceptance passed on macOS 27.0.1 with CPython 3.12.11, uv 0.7.12, and Google Chrome. All 29 tests in `test_application*.py` passed in 135.559s against the installed `workspace app` entry point, including all 5 tests in `workspace-test/tests/test_application_console_logs.py`.

- Default sheet log settings (`save_logs=True`, `retention_days=7`), private storage under `<sheet_root>/.workspace/logs/<run_id>/<YYYY-MM-DD>.ndjson` (`0o700` dirs / `0o600` files) separate from sanitized `.workspace/runs`, split UTF-8 and partial-line stdout/stderr capture, authentication/origin checks, and restoration after sheet reload, workspace navigation, and `workspace app` restart all passed.
- Disabling saving on one sheet (`Main`) stops disk writes immediately (including mid-run) while continuing live stdout/stderr streaming and leaves another sheet (`Secondary`) with its default settings and saved logs. Log I/O failure reports cleanly without failing the flow run.
- Setting retention to 2 days rotates segments daily across `.workspace/log-date` boundaries (`2026-11-18` -> `2026-11-19` -> `2026-11-20`) and automatically expires old segments and empty run directories during a continuous run's quiet period while preserving unexpired segments and surviving logs.
- `ConsoleLogStore` unit checks passed for `PHYZFLOW_LOG_DATE`, `.workspace/log-date`, injectable clock, UTC fallback, ID/stream validation, symlink rejection, and truncated NDJSON line tolerance.
- `node --check workspace/src/workspace_sdk/static/app.js` and `tests.test_architecture_deepening` + `tests.test_runner` (10 tests) passed.

Run from `workspace-test` with `./workspace[mcp]` and `./workspace-test[verification]` installed:

```sh
node --check ../workspace/src/workspace_sdk/static/app.js
/tmp/phyzflow-app-06-venv/bin/python -m unittest discover -s tests -p 'test_application*.py' -v
/tmp/phyzflow-app-06-venv/bin/python -m unittest tests.test_architecture_deepening tests.test_runner -v
```

