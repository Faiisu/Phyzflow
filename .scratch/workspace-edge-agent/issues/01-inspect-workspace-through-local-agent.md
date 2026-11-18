# 01: Inspect an Edge workspace through a local agent

**What to build:** A permitted local client starts or connects to the Workspace agent, selects a configured project, and inspects its sheets, environment, files, and source without opening a browser.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] Document and exercise a minimal local command contract with project and request identifiers, structured results, and stage-specific errors; reject unsupported operations and invalid requests.
- [ ] A client lists sheets, identifies the selected Python environment, browses files, reads source with a revision, and searches source in an installed sample project.
- [ ] File and search responses have explicit size limits and report truncation; reject unknown projects, path traversal, and symbolic-link escapes beyond permitted project roots.
- [ ] Only permitted local clients can access the agent; document how it is started and how the client is authorized.
- [ ] An integration test covers the client-to-agent success path and denied access without requiring the Web UI; existing CLI and UI project inspection still works.
