# 02: Reject invalid flows before execution

**What to build:** Let developers and CI verify project source and flow configuration before running nodes, using the same checks at each entry point.

**Blocked by:** 01 (Run the sample from a clean installation)

**Status:** resolved

- [x] Both `workspace check` and `workspace run` reject a syntax error in a nested source file, a broken import or entrypoint, a missing dependency reference, a cycle, and incorrect or missing argument bindings.
- [x] Validation uses the target interpreter to check syntax within the declared project source scope before importing the flow.
- [x] Invalid projects exit unsuccessfully before any node function runs. The diagnostic identifies the validation stage and reports a relevant file and line when available.
- [x] A valid model-backed flow passes validation without loading the model or accessing the network.

## Verification

Ran the validation acceptance tests against the installed CLI on CPython 3.12.11. Both commands rejected all seven invalid-project cases before node execution. The nested syntax case stopped before importing the flow. A model-backed fixture passed checks without weights and produced the expected score once the weights were available for execution.
