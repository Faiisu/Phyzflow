# 02: Execute loops and record iterations

**What to build:** Update `runner.py`, `history.py`, and `execution.py` to execute cyclic `LoopOutput` components iteratively with initial values, stop conditions, iteration limits, and accurate timing/iteration summaries.

**Blocked by:** 01

**Status:** resolved

- [x] Execute SCCs in topological order, recursively decomposing nested loops by earliest forward loop head.
- [x] Seed `LoopOutput` inputs with `initial` on iteration 1, update with `outputs[source]` on subsequent iterations, and close per-iteration tracked generators cleanly.
- [x] Stop when `until` returns truthy (or after `max_iterations` if `until` is omitted), and fail the loop head step with `LoopLimitError` when `until` is not satisfied within `max_iterations` (default `100`), skipping downstream steps with `blocked_by`.
- [x] Emit `iteration` and cumulative `duration_ms` on looped step events, preserve live setting changes across iterations, and record `iterations` and cumulative `duration_ms` in `RunHistory`.

## Verification

Verified via `LoopValidationAndExecutionTests` (self-loops, multi-node loops, fixed-count loops, nested loops, tracked generators inside and upstream of loops, live settings persistence, `LoopLimitError` with downstream skipping, and CLI `workspace check` / `workspace run`) and `tests/test_runner.py`.
