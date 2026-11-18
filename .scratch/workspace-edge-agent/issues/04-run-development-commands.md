# 04: Run development commands in the project environment

**What to build:** A client installs a dependency and runs project tests on the Edge device through cancellable jobs, using the project environment and reading command results remotely.

**Blocked by:** 03: Validate and run an edited flow through the agent.

**Status:** resolved

- [x] Execute an explicit program and argument list in the configured project working directory and Python environment, with agent-enforced command permissions.
- [x] Use the flow job contract to report command status and exit code, and provide bounded stdout and stderr pages with resumable output cursors.
- [x] Demonstrate installing a sample dependency into the selected environment and running a test that uses it; another project environment is unaffected.
- [x] Cancellation terminates the managed process tree and reports the resulting job state; test a command that creates a child process.
- [x] A failing test returns a nonzero exit code and useful diagnostics; missing executables, denied commands, and inaccessible environments return actionable errors.
- [x] Document development-output access, size limits, and retention; a large-output command does not grow agent memory without a bound.


## Answer

Implemented authenticated `run_command` and `read_job_output` through the public `AgentClient` request contract. Commands reuse `AgentJobs` status/cancel/project ownership, run explicit argv without a shell in a validated project cwd, and require an operator-owned per-project allowlist. `python` selects the configured interpreter without resolving its virtual-environment symlink. The command environment excludes ambient credentials and Python overrides.

Output retains at most 128 KiB per stream, pages at most 16 KiB using byte cursors, counts discarded bytes, and reports truncation. At most 32 command jobs are admitted, bounding retained command output to 8 MiB. Completion reports exit code; cancellation handles the case where the leader exits on SIGTERM but its child ignores SIGTERM, escalating for the whole process group after two seconds. Development-output authorization, retention, limits, and operating-system permission boundaries are documented in `docs/workspace-agent.md`.

### Verification

Initial TDD tracer failed through `AgentClient` with `unsupported_operation`, then passed after implementation. Reproducible focused verification from the repository root:

```sh
PYTHONPATH="$PWD/workspace/src:$PWD/workspace-test:$PWD/workspace-test/tests" \
  /Users/faiisu/projects.nosync/Phyzflow/default-workspace/.venv/bin/python \
  -m unittest test_agent_commands test_agent_jobs test_agent test_agent_edit -v
git diff --check
```

Result: **23 tests passed** on macOS / Python 3.12.11. The six new command tests cover explicit cwd and selected interpreter, nonzero exits and separate stdout/stderr, a real failed unittest with assertion diagnostics, a 1 MiB output stream retaining only 128 KiB with resumable pages, credential inheritance exclusion, denied commands/cwd escapes, missing executable/inaccessible environment, command retention rejection without eviction, project ownership, and cancellation of a child that ignores SIGTERM after the parent exits. Existing agent flow tests (including browser/UI run exclusion, source reload coordination, history privacy, interpreter selection, and cancellation) pass.

The dependency proof builds a tiny pure-Python wheel with `zipfile`, creates two clean Python environments using `venv.EnvBuilder(with_pip=True, symlinks=True)`, installs through the agent with `python -m pip install --no-index --disable-pip-version-check <wheel>`, and runs `python -m unittest test_dependency -v`. The selected environment succeeds and the second environment still reports `No module named 'edge_sample_dependency'`. It requires no package-index access or build backend.

### Handoff to ticket 05

`AgentJobs.start_command`, `get`, and `output` own command process/state/output; HTTP request handling only validates permissions and delegates. Jobs currently retain process handles, output buffers, and state in `AgentJobs.jobs`. Persist serializable status/result/output separately from live process handles in ticket 05, retain byte cursor semantics, and add durable request identity/recovery there. Retention currently rejects the 33rd command until restart; there is no eviction, durable storage, background service, restart recovery, or deduplication. No Linux device/container verification was performed in this ticket; the host's Docker daemon is unavailable. Service/central/MCP work remains outside this ticket.
