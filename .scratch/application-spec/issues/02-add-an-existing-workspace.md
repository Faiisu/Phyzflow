# 02: Add an existing project as a Workspace

**What to build:** A developer can add an existing project folder from the landing page and run its sheets using the Workspace's shared Python environment.

**Blocked by:** [01: Create a Workspace and run its first flow](01-create-and-run-a-workspace.md)

**Status:** ready-for-agent

**Spec requirements:** WS-01, WS-03

- [ ] Add a project with no environment, open it, and run a sheet using the Python 3.12 `.venv` created by Phyzflow.
- [ ] Add a project with a working Python 3.12 `.venv`. Verify reuse by importing an installed dependency in two sheets and observing the same interpreter in both.
- [ ] Add a project with a broken or incompatible `.venv`. Verify that Phyzflow identifies the problem and preserves the existing environment and project files.

