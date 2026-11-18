# Workspace Edge agent implementation map

## Notes

The numbered tickets follow the dependency sequence in [the spec](spec.md).

## Decisions-so-far

- [Ticket 05](issues/05-survive-client-disconnects.md): SQLite acceptance/outcomes and private durable job artifacts are authoritative on one local agent instance. Live control-pipe supervisors clean groups after agent death; restart never signals stored PIDs or retries interrupted effects. Identity records are not pruned. The existing 32-command lifetime admission limit is preserved and its explicit archive/new-instance maintenance is documented.

## Fog

- Ubuntu 24.04/systemd manager installation/startup/boot behavior awaits a Linux target. Real module lifecycle, disconnect, duplicate, and restart checks pass on macOS; no Linux manager result is claimed.
