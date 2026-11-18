# 08: Build and debug an Edge flow through MCP

**What to build:** An AI coding agent uses the central MCP tools to select an Edge device, inspect and modify its project, manage dependencies, validate and test changes, and repair a flow using returned diagnostics.

**Blocked by:** 07: Edit and run remotely through reconnecting connections.

**Status:** resolved

- [x] Expose discoverable MCP tools for device and project inspection, source reading and searching, file creation and patching, validation, flow and command jobs, output reads, and cancellation.
- [x] Tools require explicit device and project targeting where applicable, preserve request and revision semantics, and enforce the existing central and Edge authorization checks.
- [x] Long operations return job identifiers and bounded output rather than requiring one MCP request to remain open until the operation completes.
- [x] Connect a real MCP client and demonstrate an AI editing a flow on a selected Edge, diagnosing an intentional nested error, repairing it, and completing a successful run.
- [x] Demonstrate dependency installation and project tests through the same tools, and inspect the resulting source and completed flow run in Workspace UI.
- [x] Record reproducible setup and verification steps; exercise malformed tool arguments, denied access, disconnected targets, and stale revisions without bypassing agent validation.


## Answer

Added optional `workspace-sdk[mcp]` pinned to MCP 2.3.0 and
`python -m workspace_sdk.agent_mcp --config /absolute/private/mcp.json`.
This is a stdio MCP frontend bridging authenticated HTTPS central routing, not a
public HTTP MCP endpoint. Thirteen typed discoverable tools preserve explicit
device/project/request IDs, revision checks, Edge-owned jobs/output, and exact
mutation retry semantics by forwarding through existing `RemoteClient`. Central
and Edge keep their existing independent authorization. Credentials remain in
private owner-only files; the frontend introduces no second execution path.
Plain SDK installs do not import or require MCP.

The approved actual-client seam first failed because the MCP module was absent;
it now passes discovery/schema checks, malformed missing arguments, revision
conflict, denied devices/projects, unknown devices, actual authorized-device
disconnection, deduplicated flow start/output 42, bounded command output/cursor,
and job cancellation. Optional-extra absence explicitly skips only this case.

The implementing AI used a **real interactive MCP client** and the TLS router,
outbound connector and authenticated Edge to inspect and rename the flow, observe
an intentional nested RuntimeError at `nested/worker.py:2`, re-read that source,
choose a revision-checked repair, and obtain output 42. Through the same tools it
installed a tiny offline wheel and ran a passing project unittest importing that
dependency. Actual Chromium UI inspection confirmed the identical successful run
ID/source version and repaired source. The transcript also preserves a real
validation/run conflict and the subsequent deliberate retry with a new identity.

Full reproduction, exact results, redacted protocol request/results, browser
observations, screenshots, and limits are in
[the ticket evidence](../evidence/08/README.md). Reusable scripts live under
`workspace-test/tools/`; setup installs a normal wheel, avoiding editable links
to temporary worktrees. No tokens or private configuration were committed.

Verification on macOS/Python 3.12.11:

```sh
PYTHONPATH=workspace/src:workspace-test/tests /tmp/phyzflow-edge08-venv/bin/python -m unittest test_agent_mcp test_agent_remote -v
PYTHONPATH=workspace/src:workspace-test/tests /Users/faiisu/projects.nosync/Phyzflow/default-workspace/.venv/bin/python -m unittest test_agent_mcp -v
git diff --check
```

**14 tests passed in 41.255 seconds** in the isolated MCP environment. The plain
SDK environment cleanly reported one explicit optional-extra skip. Browser
inspection asserted run `54c1971a325c4381bef6f5337528e1a4` with source version
`cb66e6c67abab9e2fa41c684c6910f6de92c5fcbcd48cd4a8d8e5ac53012d4e0`.
The demo ran from a noneditable installed package and has been stopped cleanly;
its disposable environment/data are outside the ticket worktree. No Torch install
was required. `git diff --check` passed. Actual hardware, Linux/Docker and public
internet deployment were unavailable and are not claimed; this verifies real
TLS/process/MCP/browser behavior on one host. No push performed.
