# 07: Edit and run remotely through reconnecting connections

**What to build:** A client edits and runs a flow through the central router, loses connectivity, and reconnects to inspect or cancel the original Edge-owned job.

**Blocked by:** 06: Route an authenticated coding request to an Edge device.

**Status:** ready-for-agent

- [ ] Route the existing file mutation, validation, flow job, command job, output, and cancellation operations without duplicating their execution logic in the central service.
- [ ] Demonstrate editing a flow and running a test or flow on the selected device, with revision conflicts and execution failures propagated back to the requesting client.
- [ ] Disconnect the Edge channel and restart the central service during a long-running job; accepted Edge work continues and its original state and output are available after reconnection.
- [ ] Retries after lost acknowledgements preserve request identity and return the original mutation result or job; the same request identifier is scoped to its device and project.
- [ ] Cancellation requested while disconnected reports that it has not been delivered; after reconnection the client can cancel the original job and observe termination.
- [ ] Reconnects restore authenticated device routing without exposing another client or device's project data; verify that files, execution state, and output remain owned by the Edge.
