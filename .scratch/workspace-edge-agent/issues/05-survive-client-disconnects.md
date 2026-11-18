# 05: Keep the agent available across client disconnects

**What to build:** An operator starts the agent as a background service, disconnects the development client, and later resumes job inspection without restarting or duplicating accepted work.

**Blocked by:** 04: Run development commands in the project environment.

**Status:** resolved

- [x] Provide reproducible install, start, stop, and startup-failure instructions for one documented Edge operating system; the agent operates without a browser session.
- [x] A long-running job continues after client disconnect and a new authorized client resumes status and output from its job identifier and output cursor.
- [x] Persist accepted mutation request identities and job records on the Edge; retrying an identical request returns its original outcome or job, and conflicting reuse is rejected.
- [x] Demonstrate reconnecting after a lost start acknowledgement without applying an edit twice or launching a second command.
- [x] Completed jobs remain inspectable after agent restart; interrupted jobs are identified honestly and no orphaned managed process is silently left running.
- [x] Cover disconnect, duplicate delivery, restart recovery, and shutdown behavior using real processes through the agent entry point.


## Answer

Implemented private, device-local SQLite persistence for mutation request fingerprints/outcomes, job status/results, and the existing bounded command output. Acceptance commits before effects; job identifiers/queued records commit before process launch. Identical delivery returns its original response (including the original job identifier or error); conflicting reuse returns `request_conflict`. Fingerprints also bind configured project root/interpreter, and records are never silently pruned. Concurrent deliveries serialize behind the committed outcome. An interrupted non-job mutation returns an explicit uncertain-outcome diagnostic rather than being reapplied.

Completed jobs/output survive process restart. Active jobs recover as `interrupted` and never restart automatically. Each job has a control-pipe supervisor and a live group leader; agent death triggers group SIGTERM/SIGKILL without signaling any stored PID. Supervisors retain the exclusive state-directory lock until cleanup finishes, so replacement agents cannot overlap old managed processes. Normal cancellation/shutdown drains collectors and persists terminal outcomes. State lives outside configured projects, with a private 0700 directory and 0600 database/lock. Existing stdout/stderr caps and byte cursors remain unchanged.

Documented installation, start, stop, boot startup, and startup-failure troubleshooting for Ubuntu 24.04 LTS, with an operator-run systemd template in `docs/workspace-agent.service`. No system service is installed by the code/tests. The lifetime admission cap remains 32 command jobs; restart no longer clears it. Documentation explicitly describes archive/new-instance maintenance, disposal of old retry queues, sensitive artifacts/logs, and the absence of automatic identity pruning.

### Verification

The first TDD tracer failed through a real agent module process and `AgentClient`: identical command retry launched a second job. It now returns the exact original response before and after restart. A malformed-operation regression was also observed red (`RemoteDisconnected` for object/list operation names), then corrected to return structured `invalid_request` errors.

From the worktree root:

```sh
PYTHONPATH="$PWD/workspace/src:$PWD/workspace-test:$PWD/workspace-test/tests" \
  /Users/faiisu/projects.nosync/Phyzflow/default-workspace/.venv/bin/python \
  -m unittest test_agent_persistence test_agent_commands test_agent_jobs test_agent test_agent_edit -v
# The last added project-reconfiguration/private-state check was run separately:
PYTHONPATH="$PWD/workspace/src:$PWD/workspace-test:$PWD/workspace-test/tests" \
  /Users/faiisu/projects.nosync/Phyzflow/default-workspace/.venv/bin/python \
  -m unittest test_agent_persistence.AgentPersistenceTests.test_reconfigured_project_cannot_replay_another_roots_outcome -v
git diff --check
```

Result: **29 tests passed in the combined run, plus the final added test passed separately (30 tests total)** on macOS / Python 3.12.11. The persistence suite exercises actual module startup, a discarded HTTP start acknowledgement, six concurrent duplicate deliveries, a single recorded command effect, reconnect/output cursors, source create/patch retry, completed status/output after restart, changed-content and changed-project-root conflicts, malformed requests, private runtime permissions, active-instance startup rejection, SIGTERM cancellation, and SIGKILL cleanup of a stubborn child. It also kills the agent after an accepted cancellation has visibly reached the child but before its acknowledgement/outcome commit; reconnect returns `interrupted`/uncertain and never silently replays the mutation. Existing command, flow, UI coordination, history privacy, and environment isolation checks pass.

Ubuntu/systemd installation/manager/boot behavior has **not** been executed: this host is macOS and its Docker daemon is unavailable. The template and reproducible operator instructions are provided without claiming Linux manager verification. Commands that deliberately escape the managed process group require the documented account/cgroup boundary. Raw output may end before the final produced bytes after a crash; external effects are never transactionally rolled back.

### Handoff to ticket 06

`create_agent_server(config, port)` still returns the normal server interface; optional `config.state_dir` selects the durable local instance (default `<token_file>.state`). Its server subclass now owns jobs/state and closes them in `server_close`; call `shutdown` from another thread before closing a serving instance. The module launcher handles SIGTERM by shutting down the HTTP loop, then running that close path. No public `server.agent` accessor was added. For an embedded routing adapter, `WorkspaceAgent(projects, state)` and `AgentState(directory)` are available; the adapter must preserve the original request dictionary/identifiers, share this one durable instance, and explicitly close jobs before state. Do not create a second ledger for each connection or forward retries under newly generated identities. Central routing/authorization/MCP remain outside ticket 05.
