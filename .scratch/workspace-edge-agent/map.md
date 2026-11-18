# Workspace Edge agent implementation map

## Notes

The numbered tickets follow the dependency sequence in [the spec](spec.md).

## Decisions-so-far

- [Ticket 05](issues/05-survive-client-disconnects.md): SQLite acceptance/outcomes and private durable job artifacts are authoritative on one local agent instance. Live control-pipe supervisors clean groups after agent death; restart never signals stored PIDs or retries interrupted effects. Identity records are not pruned. The existing 32-command lifetime admission limit is preserved and its explicit archive/new-instance maintenance is documented.

- [Ticket 06](issues/06-route-authenticated-edge-requests.md): TLS long polling routes scoped authenticated clients to stable Edge device identities. The independent outbound connector reuses the existing authenticated local agent; central owns only bounded ephemeral routing. Session generations fence stale polls/replies; expiry distinguishes unavailable queued work from delivery uncertainty after fetch. Remote inspection only; reconnect/mutations follow in 07.

- [Ticket 07](issues/07-remote-edit-run-and-reconnect.md): All existing development operations now route through TLS to the sole Edge owner. Automatic reconnect resumes known sessions or re-registers after central restart; replaced connectors exit. Session heartbeats retain the lease during slow acknowledgements, while expired routes return uncertainty. Same-ID retries use the Edge ledger; central never replays work or owns job state. Real edit/run, delayed acknowledgements, original-job restart/output/cancel, and device/project isolation evidence is recorded in the ticket.

## Fog

- Ubuntu 24.04/systemd manager installation/startup/boot behavior awaits a Linux target. Real module lifecycle, disconnect, duplicate, and restart checks pass on macOS; no Linux manager result is claimed.
