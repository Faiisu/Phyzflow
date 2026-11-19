# 02: Create a Workspace and run a flow on Windows

**What to build:** A developer can create or add a Workspace from the browser, then run and debug its flow on a Windows backend using that Workspace's Python environment.

**Blocked by:** [01: Start Phyzflow and sign in on Windows](01-start-phyzflow-and-sign-in-on-windows.md)

**Status:** resolved

- [x] Create a new Workspace from the browser and verify that its Main sheet opens and runs in the Workspace's provisioned Python 3.12 environment.
- [x] Add a separate existing project with a valid Python 3.12 environment and verify that the environment is reused. Repeat with a broken or incompatible environment and verify that the application reports the problem and preserves the existing directory.
- [x] Run the support-ticket sample through the browser and verify the expected billing and urgent classification.
- [x] Force the predictor to fail. Verify that the UI identifies the failed step, marks the dependent step as skipped, and opens the nested source at the failing line.

## Verification

Commit `7a9c19b` passed remote browser acceptance on Windows 11 IoT Enterprise build `26200.9974`. A newly created Main sheet ran successfully. An existing Python 3.12 environment was reused. Broken and Python 3.11 environments were rejected with their existing files preserved. The ticket sample returned billing and urgent, and the failure case marked Predict failed and Decide skipped and selected line 3 of the nested source. Three related macOS browser tests passed.
