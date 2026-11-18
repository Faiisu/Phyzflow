# 02: Add an existing project as a Workspace

**What to build:** A developer can add an existing project folder from the landing page and run its sheets using the Workspace's shared Python environment.

**Blocked by:** [01: Create a Workspace and run its first flow](01-create-and-run-a-workspace.md)

**Status:** resolved

**Spec requirements:** WS-01, WS-03

- [x] Add a project with no environment, open it, and run a sheet using the Python 3.12 `.venv` created by Phyzflow.
- [x] Add a project with a working Python 3.12 `.venv`. Verify reuse by importing an installed dependency in two sheets and observing the same interpreter in both.
- [x] Add a project with a broken or incompatible `.venv`. Verify that Phyzflow identifies the problem and preserves the existing environment and project files.


## Verification

Browser acceptance passed on macOS 27.0.1 (26A434) with Google Chrome, CPython 3.12.11, and uv 0.7.12. The tests launch the installed `workspace app` command and add existing project folders from the landing page. They run the existing Main sheet in a newly provisioned Python 3.12 environment and verify that its source is unchanged. They also install a local wheel in a pre-existing environment and observe the same interpreter and dependency output from two sheets. Broken and Python 3.11.15 environments produce actionable errors and retain their original contents and project source.

The tests also reject a duplicate folder reached through a symlink or a case alias, nested Workspace roots in either direction, and paths overlapping application state. Rejected paths receive no new environment. The create-and-run acceptance tests pass alongside the add scenarios.

Run from the repository root with the SDK and Playwright installed, Google Chrome available, and uv able to find Python 3.12 and 3.11.

```sh
PYTHONPATH=workspace-test python -m unittest discover -s workspace-test/tests -p 'test_application*.py'
```
