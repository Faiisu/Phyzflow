# 01: Inspect an Edge workspace through a local agent

**What to build:** A permitted local client starts or connects to the Workspace agent, selects a configured project, and inspects its sheets, environment, files, and source without opening a browser.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] Document and exercise a minimal local command contract with project and request identifiers, structured results, and stage-specific errors; reject unsupported operations and invalid requests.
- [x] A client lists sheets, identifies the selected Python environment, browses files, reads source with a revision, and searches source in an installed sample project.
- [x] File and search responses have explicit size limits and report truncation; reject unknown projects, path traversal, and symbolic-link escapes beyond permitted project roots.
- [x] Only permitted local clients can access the agent; document how it is started and how the client is authorized.
- [x] An integration test covers the client-to-agent success path and denied access without requiring the Web UI; existing CLI and UI project inspection still works.

## Answer

Implemented a standard-library loopback HTTP agent with a private bearer-token file, explicit project/environment configuration, and a Python client. The inspection contract supports project/sheet discovery, bounded directory listing, revision-bearing file reads, and bounded literal code search. Unknown projects, unsupported requests, unauthorized callers, traversal, and symbolic-link targets are rejected. The agent does not import or execute inspected source.

The public client/server seam was exercised with six integration tests, including module startup against the installed sample consumer and consistency with existing CLI sheet discovery. TDD evidence: the initial inspection test failed because the agent module did not exist; a subsequent bounded empty-directory traversal test failed before the traversal cap was implemented.

Verification (from the checkout, using a Python environment with the SDK installed):

- `PYTHONPATH="$PWD/workspace/src:$PWD/workspace-test" python -m unittest discover -s workspace-test/tests -p test_agent.py -v`: 6 passed.
- The same invocation with `test_architecture_deepening.py`: 5 passed, including existing UI HTTP transport and sheet discovery behavior.
- The same invocation with `test_validation.py`: 2 passed.
- `git diff --check`: passed.

Startup, the request contract, authentication, and operational limits are documented in the Workspace development agent guide. Background-service lifecycle and mutations remain assigned to subsequent tickets. The local transport requires a Unix account supporting token-file ownership and permissions; another process already running as that account is outside the isolation boundary.
