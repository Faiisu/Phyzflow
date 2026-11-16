# 03: Run and debug the sample in the Web UI

**What to build:** Let a developer launch a localhost Web UI from the project CLI, inspect the declared flow, run the sample, and trace a failing step back to the source.

**Blocked by:** 01 (Run the sample from a clean installation)

**Status:** resolved

- [x] The graph shows `Prepare → Predict → Decide` and the direct `Prepare → Decide` edge, with node source locations.
- [x] Running the shared sample input returns the same expected output as the CLI. The browser displays live statuses from the server-side Runner and uses the shared project validator.
- [x] A new user can identify `Predict`, the exception, and the nested source file from the UI. The run shows `Decide` as `skipped`.
- [x] After changing the source and restarting the CLI-launched UI, another run uses the updated code.

## Verification

The installed CLI served the UI in Chrome. Browser verification covered both graph edges into Decide, sample output, a live running status, the skipped downstream step, and the nested exception with a source line. Restarting the CLI loaded an edited predictor and returned its new result. Reviewed desktop and mobile rendering.
