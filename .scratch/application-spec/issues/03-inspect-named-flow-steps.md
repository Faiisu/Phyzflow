# 03: Inspect a flow step by step

**What to build:** A developer can follow named steps in a Python flow from the web UI and identify where an error began.

**Blocked by:** [01: Create a Workspace and run its first flow](01-create-and-run-a-workspace.md)

**Status:** resolved

**Spec requirements:** FLOW-01, FLOW-02

- [x] Run a flow with multiple connected named steps. Inspect each step's status, timing, and place in the flow.
- [x] Publish progress and a metric from within a step. Verify that both appear in the UI while that step runs.
- [x] Force a step to fail and verify the UI identifies that step and its error.


## Verification

The browser acceptance test passed on Apple Silicon running macOS 27.0.1 (26A434), Google Chrome 157.0.8103.79, and CPython 3.12.11. It launches the installed `workspace app`, signs in, and opens a new Workspace. The test follows the Prepare → Validate → Report connections and inspects status and timing. While Prepare is still running, its progress and throughput update from 25% and 17 rows per second to 60% and 42 rows per second without resetting the start time. The completed run displays the metric explicitly recorded for it. A second run shows Validate failed with `ValueError`, opens the nested file at the failing line, and explains why Report was skipped.

Run from the repository root with the SDK and Playwright installed and Google Chrome available.

```sh
PYTHONPATH=workspace-test python -m unittest discover -s workspace-test/tests -p test_application_flow_steps.py -v
```
