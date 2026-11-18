# 06: Route an authenticated coding request to an Edge device

**What to build:** A client discovers permitted Edge devices through the central service and inspects a chosen project over an authenticated outbound connection from that device.

**Blocked by:** 05: Keep the agent available across client disconnects.

**Status:** resolved

- [x] An Edge agent registers with a stable device identity and connects outward over an authenticated encrypted channel; no inbound public Edge UI port is required.
- [x] The central service lists devices visible to the client and distinguishes connected devices from unavailable ones.
- [x] Route project inspection requests using device, project, and request identifiers; correlate each result to its originating client request.
- [x] Central authorization rejects unauthorized device access and the Edge agent independently enforces its permitted project scope.
- [x] An integration demonstration with two separately identified agents returns distinct project contents from the requested device without cross-device routing.
- [x] Unavailable targets, failed authentication, and disconnected requests return explicit errors rather than being reported as successful or retried onto a different device.


## Answer

Implemented `workspace_sdk.agent_remote`: a TLS-only central router, authenticated SDK `RemoteClient`, and outbound `EdgeConnector` plus operator module launchers. The connector forwards the unchanged request to the existing authenticated loopback `AgentClient`; `create_agent_server` remains the sole local durable state/jobs owner. Stable device IDs and separate private per-device/per-client token files provide explicit device grants. Central stores credentials and bounded ephemeral routes only, never project source or authoritative job records. Both central and connector permit only inspection/list/read/search in this ticket.

Client-visible devices distinguish connected and unavailable with a three-second lease. Registration issues an opaque session generation; replacing it invalidates old work and rejects stale polls/replies. Device identity, current session, opaque delivery identity, and original request ID bind replies. Expired queued reads are removed rather than delivered later. If a device already fetched a request and its reply is lost, the caller receives `delivery_unknown`, never a false success or a claim that execution did not happen. No alternate device retry occurs. Routes cap at 32, simultaneous connections at 64, inner requests at 64 KiB, and relay messages at 1 MiB. TLS validates CA and hostname without a bypass, rejects HTTP origins and credential-bearing URLs, and never follows redirects.

Documented central TLS/configuration, per-client grants, independent connector configuration/start/stop, SDK calls, endpoint contract, timing/size bounds, and deployment limits in `docs/workspace-agent.md`. Tokens appear only in private files/configuration or authorization headers, never process arguments or URLs. The local Edge project/path checks remain independent of central device authorization.

### Verification

The first TDD tracer failed because the remote module was absent, then passed through an actual HTTPS central and two outbound connectors to two authenticated local agents with different source. Tests use temporary OpenSSL certificates with a localhost subject alternative name and normal CA/hostname verification.

```sh
PYTHONPATH="$PWD/workspace/src:$PWD/workspace-test:$PWD/workspace-test/tests" \
  /Users/faiisu/projects.nosync/Phyzflow/default-workspace/.venv/bin/python \
  -m unittest test_agent_remote test_agent test_agent_edit -v
# Final focused rerun after redirect hardening and inspect/list/search assertions:
PYTHONPATH="$PWD/workspace/src:$PWD/workspace-test/tests" \
  /Users/faiisu/projects.nosync/Phyzflow/default-workspace/.venv/bin/python \
  -m unittest test_agent_remote -v
git diff --check
```

Result: **17 tests passed in the combined run; all 7 remote tests passed again after final hardening** on macOS / Python 3.12.11. Evidence covers concurrent same-request-ID reads routed to distinct device contents, all four remote inspection operations, restricted device discovery/grants, wrong client/device credentials, independent local project/path rejection, disabled remote mutations, untrusted CA and mismatched hostname rejection, disconnection, stale session poll/reply rejection, mismatched request correlation, expired queued removal and late reply rejection, and real central/connector module startup and SIGTERM shutdown using private configuration.

This is a real network/TLS demonstration on one host, not physical Edge hardware or a public deployment. The standard-library server is documented for controlled development networks. Automatic reconnect, remote mutation/job behavior, central restart recovery, and MCP remain tickets 07–08; no service installation is performed by tests.

### Handoff to ticket 07

`RemoteClient(url, token, cafile=None)` exposes `list_devices()` and `request(request_id, device_id, project_id, operation, arguments=None)`. `EdgeConnector(url, device_id, token, local_client, cafile=None).run(stop_event)` currently registers one session and exits on connection/authentication failure; `local_client` must be the existing authenticated loopback `AgentClient`. `create_router_server(config, port=0, host="127.0.0.1")` returns the usual server interface; shutdown from another thread, then close. Config and endpoint fields are documented in the operator guide. Extend both inspection allowlists deliberately for mutations; preserve the immutable inner request ID and use the existing Edge durable ledger for retry. Retain honest `delivery_unknown` for fetched work and prevent queued expired mutations executing after an unavailable response. Registration generations must continue fencing stale sessions when reconnect support is added.
