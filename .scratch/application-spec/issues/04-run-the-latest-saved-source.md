# 04: Use saved source on the next run

**What to build:** A developer can save Python edits while a sheet runs and see the edits take effect on its next run.

**Blocked by:** [01: Create a Workspace and run its first flow](01-create-and-run-a-workspace.md)

**Status:** resolved

**Spec requirements:** FLOW-05

- [x] Start a flow that pauses before completing, save an observable source change while it is active, and release the pause. Verify that the active run completes with its original behavior.
- [x] Start the sheet again and verify that the saved change takes effect without a manual reload.
- [x] Reuse the same acceptance case to check existing source reload behavior before making changes.

## Verification

Browser acceptance passed on Apple Silicon running macOS 27.0.1 (26A434), Google Chrome 157.0.8103.79, CPython 3.12.11, and uv 0.7.12. The test launches the installed `workspace app`, signs in, and opens a new Workspace. It pauses the flow, saves edits to `flow.py` and a node helper through the application, and writes an edit to an ordinary Python module on disk. A second case saves all three files on disk. In both cases the running flow returns the original values, including from Python imports first made after the saves. The completed result stays visible after the source change is detected. The next Run returns the updated values without a reload action.

Before the fix, the browser disabled Save during the run. The on-disk case showed that the active run read the updated value from an ordinary lazy import outside the configured source list. The configured helper and already loaded `flow.py` still returned their original values.

Run from the repository root with the SDK installed with its `mcp` extra, Playwright installed, and Google Chrome available.

```sh
PYTHONPATH=workspace-test python -m unittest discover -s workspace-test/tests -p test_application_saved_source.py -v
```
