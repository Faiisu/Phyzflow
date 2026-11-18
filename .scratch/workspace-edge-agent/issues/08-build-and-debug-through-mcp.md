# 08: Build and debug an Edge flow through MCP

**What to build:** An AI coding agent uses the central MCP tools to select an Edge device, inspect and modify its project, manage dependencies, validate and test changes, and repair a flow using returned diagnostics.

**Blocked by:** 07: Edit and run remotely through reconnecting connections.

**Status:** ready-for-agent

- [ ] Expose discoverable MCP tools for device and project inspection, source reading and searching, file creation and patching, validation, flow and command jobs, output reads, and cancellation.
- [ ] Tools require explicit device and project targeting where applicable, preserve request and revision semantics, and enforce the existing central and Edge authorization checks.
- [ ] Long operations return job identifiers and bounded output rather than requiring one MCP request to remain open until the operation completes.
- [ ] Connect a real MCP client and demonstrate an AI editing a flow on a selected Edge, diagnosing an intentional nested error, repairing it, and completing a successful run.
- [ ] Demonstrate dependency installation and project tests through the same tools, and inspect the resulting source and completed flow run in Workspace UI.
- [ ] Record reproducible setup and verification steps; exercise malformed tool arguments, denied access, disconnected targets, and stale revisions without bypassing agent validation.
