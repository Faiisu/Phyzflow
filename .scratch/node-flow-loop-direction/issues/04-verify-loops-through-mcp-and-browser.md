# 04: Verify loops through MCP and browser

**What to build:** Update MCP guidance and validation/execution responses, document `LoopOutput` in `workspace/README.md`, and verify loop flows end-to-end via unit tests, CLI, MCP client, and Chrome Playwright tests.

**Blocked by:** 01, 02, 03

**Status:** resolved

- [x] Update `mcp_flow_guidance.py` and `sheet_worker.py` (`validate_flow`) with loop guidance and validated loop edges.
- [x] Verify `validate_flow`, `run_flow`, and `inspect_execution` on a loop flow through an authenticated MCP client with Workspace grant enforcement.
- [x] Verify loop graph layout, dashed directional loop edges without card intersections, self-loops, Inspector navigation, and live/saved execution metrics in Chrome.
- [x] Document `LoopOutput` usage and design rationale in `workspace/README.md`.

## Verification

Verified via `LoopMcpTests.test_validate_run_and_inspect_loop_flow_through_mcp` (authenticated Streamable HTTP MCP client exercising `open_sheet`, `read_file`, `write_file`, `validate_flow`, `close_sheet`, grant denial, `run_flow`, and `inspect_execution` for both success and `LoopLimitError`) and `test_application_mcp_commands.py`.
