# 02: Create a Workspace and run a flow on Windows

**What to build:** A developer can create or add a Workspace from the browser, then run and debug its flow on a Windows backend using that Workspace's Python environment.

**Blocked by:** [01: Start Phyzflow and sign in on Windows](01-start-phyzflow-and-sign-in-on-windows.md)

**Status:** ready-for-agent

- [ ] Create a new Workspace from the browser and verify that its Main sheet opens and runs in the Workspace's provisioned Python 3.12 environment.
- [ ] Add a separate existing project with a valid Python 3.12 environment and verify that the environment is reused. Repeat with a broken or incompatible environment and verify that the application reports the problem and preserves the existing directory.
- [ ] Run the support-ticket sample through the browser and verify the expected billing and urgent classification.
- [ ] Force the predictor to fail. Verify that the UI identifies the failed step, marks the dependent step as skipped, and opens the nested source at the failing line.
