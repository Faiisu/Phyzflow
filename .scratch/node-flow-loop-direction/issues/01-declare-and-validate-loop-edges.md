# 01: Declare and validate loop edges

**What to build:** Add `LoopOutput` to `workspace_sdk` and update shared project validation and AST recovery parsing so flows can declare valid self-loops, multi-node loops, and nested loops while still rejecting unannotated forward `Output` cycles.

**Blocked by:** None.

**Status:** resolved

- [x] Add `LoopOutput(step_id, initial=None, *, until=None, max_iterations=None)` in `flow.py` and export it in `__init__.py`.
- [x] Validate `LoopOutput` targets, reachability from consumer to producer in the forward `Output` DAG, callable `until`, positive integer `max_iterations`, non-generator source, and presence of `until` or `max_iterations`.
- [x] Preserve `Dependency cycle` validation errors for forward `Output`-only cycles.
- [x] Expose `loop_dependencies` and `"kind": "loop"` arguments in `web.py` (`/project`), `sheets.py` (`declared_graph`), `workspace_ui/dashboard_data.py`, and `validate_flow` (`loops` summary).

## Verification

Verified via `LoopValidationAndExecutionTests.test_validates_loop_edges_and_rejects_invalid_loop_declarations` and `test_declared_graph_parses_loop_dependencies_for_recovery` in `workspace-test/tests/test_loop_direction.py`, plus `tests/test_validation.py`.
