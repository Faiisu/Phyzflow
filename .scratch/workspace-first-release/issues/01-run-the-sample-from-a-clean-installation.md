# 01: Run the sample from a clean installation

**What to build:** Let a developer install the Workspace SDK as a dependency of the sample consumer project and run the same ticket-classification flow from Python and from the CLI.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] Record the first workload, candidate deployment target, and Python version so the demo has a concrete target to test.
- [x] In a fresh Python environment, install from the repository, call a node directly, and run the same sample input through the Python API and `workspace run`. Both runs return the expected output.
- [x] The sample runs `Prepare → Predict → Decide`, and `Decide` receives both the prepared input and the prediction.
- [x] The CLI uses the project's shared validation and can check the valid sample. The Runner exposes run and step identifiers, statuses, timestamps, and source locations that the UI can consume.
- [x] A forced exception inside a nested helper of `Predict` identifies the node and failing source line. The run fails and `Decide` is marked `skipped`.

## Verification

Installed both packages into a new CPython 3.12.11 environment. Verified direct node invocation, matching Python API and CLI output, the shared check, step event metadata, and a nested predictor failure with the file, line, and skipped downstream step. See the sample project README for the candidate deployment target.
