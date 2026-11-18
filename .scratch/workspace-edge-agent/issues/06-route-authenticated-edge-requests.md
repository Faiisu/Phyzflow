# 06: Route an authenticated coding request to an Edge device

**What to build:** A client discovers permitted Edge devices through the central service and inspects a chosen project over an authenticated outbound connection from that device.

**Blocked by:** 05: Keep the agent available across client disconnects.

**Status:** ready-for-agent

- [ ] An Edge agent registers with a stable device identity and connects outward over an authenticated encrypted channel; no inbound public Edge UI port is required.
- [ ] The central service lists devices visible to the client and distinguishes connected devices from unavailable ones.
- [ ] Route project inspection requests using device, project, and request identifiers; correlate each result to its originating client request.
- [ ] Central authorization rejects unauthorized device access and the Edge agent independently enforces its permitted project scope.
- [ ] An integration demonstration with two separately identified agents returns distinct project contents from the requested device without cross-device routing.
- [ ] Unavailable targets, failed authentication, and disconnected requests return explicit errors rather than being reported as successful or retried onto a different device.
