# Node flow loop direction

This feature allows Phyzflow node flows to declare, validate, execute, inspect, and visualize directed loop (back-edge) connections.

## Goals

1. **Python SDK & Validation (`flow.py`, `project.py`, `sheets.py`):**
   - Introduce `LoopOutput(step_id, initial=None, *, until=None, max_iterations=None)` exported from `workspace_sdk`.
   - Keep `Output` dependencies strictly acyclic (catching accidental forward cycles as `Dependency cycle`), while validating that every `LoopOutput` references a known non-generator step reachable from its consumer step in the forward `Output` graph (forming a self-loop or multi-node cycle) and defines either `until` or `max_iterations`.
   - Share validation and loop metadata across CLI (`workspace check`), UI (`/project`, recovery `declared_graph`), and MCP (`validate_flow`).

2. **Runner & Execution History (`runner.py`, `history.py`, `execution.py`, `web.py`):**
   - Execute strongly connected components induced by `LoopOutput` edges iteratively from the earliest loop head in forward topological order, seeding back-edges with `initial` on iteration 1 and updating them with the source step's output after each iteration.
   - Stop cleanly when `until` returns truthy (or after `max_iterations` when `until` is omitted), and fail the loop head step with `LoopLimitError` if `until` is not satisfied within `max_iterations` (default `100`).
   - Emit `iteration` and cumulative `duration_ms` on looped step events and preserve first `started_at`, cumulative `duration_ms`, and `iterations` in `RunHistory` and `inspect_execution`.

3. **Flow Graph UI (`edge-routing.js`, `graph.js`, `inspector.js`, `display.js`, `run-controls.js`, `app.css`):**
   - Lay out nodes using forward `Output` dependencies without crashing on cycles.
   - Route `LoopOutput` edges along clear loop arcs (below or above spanned cards, including self-loops) without intersecting any card interior, using dashed lines, a dedicated loop color, a mid-path directional arrowhead, and target-port arrowheads.
   - Distinguish loop bindings in edge labels, graph summary, node iteration counters, and the Inspector Inputs/Feeds/Execution views.

4. **MCP & Documentation:**
   - Update `validate_flow`, `inspect_execution`, `mcp_flow_guidance.py`, and `workspace/README.md`, and verify loop flows end-to-end through an MCP client and Playwright browser tests.

## Tickets

| Issue | Scope |
| --- | --- |
| [01](issues/01-declare-and-validate-loop-edges.md) | `LoopOutput` type, validation rules, and AST recovery graph support |
| [02](issues/02-execute-loops-and-record-iterations.md) | SCC loop runner, stop conditions, and run history iteration tracking |
| [03](issues/03-render-loop-direction-and-inspector-ui.md) | Loop edge routing, dashed directional arrows, labels, and Inspector UI |
| [04](issues/04-verify-loops-through-mcp-and-browser.md) | MCP validation/execution verification, browser tests, and docs |
