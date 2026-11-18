# 07: Edit and run remotely through reconnecting connections

**What to build:** A client edits and runs a flow through the central router, loses connectivity, and reconnects to inspect or cancel the original Edge-owned job.

**Blocked by:** 06: Route an authenticated coding request to an Edge device.

**Status:** resolved

- [x] Route the existing file mutation, validation, flow job, command job, output, and cancellation operations without duplicating their execution logic in the central service.
- [x] Demonstrate editing a flow and running a test or flow on the selected device, with revision conflicts and execution failures propagated back to the requesting client.
- [x] Disconnect the Edge channel and restart the central service during a long-running job; accepted Edge work continues and its original state and output are available after reconnection.
- [x] Retries after lost acknowledgements preserve request identity and return the original mutation result or job; the same request identifier is scoped to its device and project.
- [x] Cancellation requested while disconnected reports that it has not been delivered; after reconnection the client can cancel the original job and observe termination.
- [x] Reconnects restore authenticated device routing without exposing another client or device's project data; verify that files, execution state, and output remain owned by the Edge.


## Answer

Enabled the existing source edits, validation/flow jobs, development command jobs,
status, output, and cancellation through the authenticated TLS relay. Both central
and connector enforce the explicit operation allowlist; Edge's existing authenticated
`AgentClient` remains the sole execution/state path. No extra agent, process manager,
project copy, or central job database was introduced. Inner request IDs and project
IDs are unchanged, so the existing durable Edge ledger owns retry deduplication.

The connector retries network/truncated-response failures with 0.25–2 second
backoff, resumes recognized sessions, and registers again after central restart
(`session_lost`). A replaced session (`stale_session`) exits instead of reclaiming
routing and causing takeover storms. An authenticated heartbeat every half second
keeps the three-second device lease alive during slow local dispatch. The original
three-second route deadline still returns honest `delivery_unknown` after fetch;
no connector automatically replays execution. Local acknowledgement loss also
returns uncertainty. Explicit same-ID retries recover the original Edge result/job.
Offline/expired queued work is removed, and cancellation requires explicit retry.

TLS CA/hostname validation, per-device/client credentials, grants, session and
reply fencing, no-redirect policy, bounded queues/messages/connections, and silent
credential/payload logging remain intact. Public `RemoteClient.request` and
`EdgeConnector` constructor signatures are unchanged for ticket 08. Added the
session-authenticated `/edge/heartbeat` endpoint and documented reconnect and
retry semantics in `docs/workspace-agent.md`.

### Verification

TDD failures observed before implementation: remote edit returned
`unsupported_operation`; central restart killed the original connector with a
connection reset and left the job unreachable; delayed real local HTTP
acknowledgement falsely marked the device unavailable after three seconds.
Each tracer now passes through actual TLS central routing, outbound connector,
and authenticated real local agent. The delay fixture is a real loopback HTTP
proxy holding an acknowledgement after real Edge execution, not a fake agent.

```sh
PYTHONPATH="$PWD/workspace/src:$PWD/workspace-test/tests" \
  /Users/faiisu/projects.nosync/Phyzflow/default-workspace/.venv/bin/python \
  -m unittest test_agent_remote test_agent_commands test_agent_edit -v
git diff --check
```

Result: **23 tests passed in 52.923 seconds** (13 remote, six command, four source-edit tests) on macOS / Python 3.12.11; `git diff --check` passed.

The remote cases cover edit/validate/run success, runtime and syntax diagnostic
propagation, stale revisions, expired queued mutation removal, unchanged successful
mutation retry responses, changed-content identity conflicts, and lost command
acknowledgement returning the original job without another process (one execution
marker and one stdout line). Identical mutation IDs are independent on two devices
and two projects, and foreign job/output reads are rejected. A real long command
continues across central shutdown/rebind; the same connector reconnects and reads
then cancels the original job. Separate disconnection coverage proves offline
cancellation is not delivered or queued and an explicit retry cancels the original
still-running job. A replaced connector exits without invalidating its successor.
Existing authentication, TLS trust/hostname, stale reply/session, two-device routing,
and real module launcher tests continue to pass.

Limits: demonstrated on one macOS host using actual network/TLS and child processes,
not physical Edge hardware or a public deployment. MCP is ticket 08. No host service
was installed, no credentials logged, and no remote push performed.
