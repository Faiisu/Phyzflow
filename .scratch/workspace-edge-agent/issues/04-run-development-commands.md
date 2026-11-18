# 04: Run development commands in the project environment

**What to build:** A client installs a dependency and runs project tests on the Edge device through cancellable jobs, using the project environment and reading command results remotely.

**Blocked by:** 03: Validate and run an edited flow through the agent.

**Status:** ready-for-agent

- [ ] Execute an explicit program and argument list in the configured project working directory and Python environment, with agent-enforced command permissions.
- [ ] Use the flow job contract to report command status and exit code, and provide bounded stdout and stderr pages with resumable output cursors.
- [ ] Demonstrate installing a sample dependency into the selected environment and running a test that uses it; another project environment is unaffected.
- [ ] Cancellation terminates the managed process tree and reports the resulting job state; test a command that creates a child process.
- [ ] A failing test returns a nonzero exit code and useful diagnostics; missing executables, denied commands, and inaccessible environments return actionable errors.
- [ ] Document development-output access, size limits, and retention; a large-output command does not grow agent memory without a bound.
