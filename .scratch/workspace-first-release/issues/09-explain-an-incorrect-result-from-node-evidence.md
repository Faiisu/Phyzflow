# 09: Explain an incorrect result from node evidence

**What to build:** Let a developer identify the node responsible for a run that finishes successfully but produces an incorrect result by inspecting evidence in local run history.

**Blocked by:** 08 (Recover local run history across browser disconnects)

**Status:** resolved

- [x] Node code can record decision reasons, and a developer can opt in to input and output examples for a selected node.
- [x] The UI shows decision reasons and opted-in examples for the relevant run and node. Nodes without sample capture enabled retain the default metadata-only behavior for inputs and outputs.
- [x] Using a fixture with a successful but incorrect result, a new user can identify the responsible node and point to the evidence in the UI.

## Verification

An independent reviewer followed the [evidence demo](../../../workspace-test/README.md#diagnose-an-incorrect-result) in a temporary project without inspecting its source or opening the answer. The successful run returned `billing` for a technical ticket. In Run history, the reviewer used Predict's normalized input, `billing` output sample, and fixed-category decision reason to identify Predict as the first incorrect node. Prepare and Decide showed input and output metadata only.

All eight Workspace acceptance tests, both Linux ARM64 Docker integration tests, Pyright, and the JavaScript syntax check passed. A Runner regression test verified that an unsupported sample leaves the original object and its contents untouched. Three rounds of the trained workload benchmark with default metadata-only capture stayed within Ticket 07's limits.
