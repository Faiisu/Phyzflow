# 03: Validate and run an edited flow through the agent

**What to build:** A client edits a flow, validates it, starts it as an agent-owned job, and reads its output or failure diagnostics while the Workspace UI can inspect the same completed run.

**Blocked by:** 02: Create and edit flow source through the agent.

**Status:** resolved

- [x] Validation uses the selected project interpreter and shared project checks; a nested syntax or import failure identifies its stage and location before node execution.
- [x] Starting a flow returns a job identifier; clients can inspect queued, running, succeeded, failed, or cancelled state and request cancellation.
- [x] Successful runs expose their output and source version; failed runs identify the node and source location, including downstream nodes skipped because of that failure.
- [x] The agent and UI coordinate execution for the same sheet and share completed flow history; a conflicting start is reported clearly.
- [x] Integration coverage edits and runs a successful flow, diagnoses a nested failure, and confirms that the UI shows matching completed run information.
- [x] Keep request values and request-derived exception messages out of stored flow history by default; explicit development diagnostics retain access controls.


## Answer

Implemented `validate_flow`, `run_flow`, `get_job`, and `cancel_job` through the authenticated AgentClient boundary. Jobs use the configured project interpreter in a fresh process; execution shares Runner/settings/history with the UI. POSIX sheet reservations exclude simultaneous UI/agent runs and reload races. Open browser pages discover agent starts, follow safe bounded state, defer source reload, and read the matching completed history/source version. Cancellation terminates the worker process group and releases its reservation before exposing terminal state. Successful output remains available when history writing fails.

Contracts and current limits are documented in [Workspace development agent](../../../docs/workspace-agent.md). Shared live state persists only status/timing/source metadata, bounded to 256 entries and 128 KiB; raw request/output/preview/traceback values remain excluded. Job results remain memory-only until ticket 05. Agent flow jobs target Linux/macOS; Windows UI imports retain their existing behavior through a conditional lock backend.

### Verification

The browser tracer first failed because an already-open sheet stayed `Ready` during an agent-owned run. After implementation, tests prove live discovery, refresh reconnect, disabled Run, UI run/reload conflict, deferred reload after editing, unchanged original result/source version, and the shared completed history. Reverse UI-to-agent exclusion, cancellation/restart, independent sheets, configured interpreter selection, separate-project import isolation, nested validation/failure/skips, privacy, and history-write failure are also covered.

Run from the repository root with the installed project Python and explicit absolute source paths:

```sh
export PYTHONPATH="$PWD/workspace/src:$PWD/workspace-test:$PWD/workspace-test/tests"
/Users/faiisu/projects.nosync/Phyzflow/default-workspace/.venv/bin/python -m unittest test_agent test_agent_edit test_agent_jobs test_validation test_web_failure_origin -v
/Users/faiisu/projects.nosync/Phyzflow/default-workspace/.venv/bin/python -m unittest test_auto_reload -v
```

Results: 20 focused tests passed (including seven new AgentClient/browser job tests); four existing automatic reload browser tests passed. `git diff --check` passed.

Additional checks were limited by existing environment/baseline issues: `test_runner` cannot import because this environment has no `torch`; the existing disconnected-history browser fixture also imports `torch` and cannot start. `test_execution_timing` times out at line 42 finding `Run Parallel Pipeline`; the identical failure was reproduced on the unchanged integration branch at `640111c`. No unrelated UI or dependency fixes were added.
