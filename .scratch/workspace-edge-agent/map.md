# Workspace Edge agent implementation map

## Notes

The numbered tickets follow the dependency sequence in [the spec](spec.md).

## Decisions-so-far

- [Ticket 01](issues/01-inspect-workspace-through-local-agent.md): Standard-library loopback HTTP agent with a private bearer-token file and explicit project/environment configuration exposes project/sheet discovery, bounded listing/search, and revision-bearing file reads without importing or executing source.

- [Ticket 02](issues/02-edit-flow-source-through-agent.md): Revision-checked `write_file` and `apply_patch` reuse the shared atomic source editor with conflict/boundary enforcement while preserving browser drafts and active-run reload deferral.

- [Ticket 03](issues/03-validate-and-run-edited-flow.md): `validate_flow`, `run_flow`, `get_job`, and `cancel_job` execute in project-interpreter subprocesses sharing Runner/settings/history, POSIX sheet locks, and default payload privacy with the Web UI.

- [Ticket 04](issues/04-run-development-commands.md): Allowlisted `run_command` and `read_job_output` run in validated project directories with a minimal environment, 128 KiB per-stream caps, 16 KiB byte-cursor paging, a 32-command cap, and process-group cancellation.

- [Ticket 05](issues/05-survive-client-disconnects.md): SQLite acceptance/outcomes and private durable job artifacts are authoritative on one local agent instance. Live control-pipe supervisors clean groups after agent death; restart never signals stored PIDs or retries interrupted effects. Identity records are not pruned. The existing 32-command lifetime admission limit is preserved and its explicit archive/new-instance maintenance is documented.

- [Ticket 06](issues/06-route-authenticated-edge-requests.md): TLS long polling routes scoped authenticated clients to stable Edge device identities. The independent outbound connector reuses the existing authenticated local agent; central owns only bounded ephemeral routing. Session generations fence stale polls/replies; expiry distinguishes unavailable queued work from delivery uncertainty after fetch. Remote inspection only; reconnect/mutations follow in 07.

- [Ticket 07](issues/07-remote-edit-run-and-reconnect.md): All existing development operations now route through TLS to the sole Edge owner. Automatic reconnect resumes known sessions or re-registers after central restart; replaced connectors exit. Session heartbeats retain the lease during slow acknowledgements, while expired routes return uncertainty. Same-ID retries use the Edge ledger; central never replays work or owns job state. Real edit/run, delayed acknowledgements, original-job restart/output/cancel, and device/project isolation evidence is recorded in the ticket.

- [Ticket 08](issues/08-build-and-debug-through-mcp.md): Optional MCP 2.3.0 stdio frontend bridges existing authenticated central HTTPS, with 13 typed explicit-target tools and unchanged Edge ownership/retry semantics. Actual interactive AI discovery/edit/nested-error/repair, offline dependency/test jobs, and same run/source inspection in Chromium are preserved in [evidence](evidence/08/README.md). Real MCP-to-TLS-to-Edge authorization, stale revision, bounded output/cancel and disconnected-target checks pass; plain SDK discovery skips only optional MCP tests. No public HTTP MCP service is claimed.

- Review fixes: external terminal runs refresh open-browser history even when the full run falls between live polls; repeated snapshots and UI-owned streams do not duplicate refreshes. Flow validation/execution now share the command minimal environment, with operator-only `inference_environment` values reserved for flow workers. Selected interpreter paths and normal installed-SDK imports are preserved. See [review evidence](review.md).

## Fog

- Ubuntu 24.04/systemd manager installation/startup/boot behavior awaits a Linux target. Real module lifecycle, disconnect, duplicate, and restart checks pass on macOS; no Linux manager result is claimed.
