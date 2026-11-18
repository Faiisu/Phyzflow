# Workspace Edge agent review and resolutions

## Scope and review axes

The review covered the implementation of [the specification](spec.md) and its eight tickets through integration commit `f09ef16` (`feature/workspace-edge-agent`). Standards and specification reviews were performed independently against the implementation. This follow-up implements all confirmed findings in one fresh worktree on `feature/edge-agent-review-fixes`, based on that integration tip. The latest integration branch was merged before handoff (`Already up to date`).

### Standards

**Original finding, P2:** `static/app.js` refreshed completed history only after observing `running=true` followed by `running=false`. An external agent flow completing entirely between 250 ms live polls rendered terminal events but never appeared in the already-open browser's history list.

**Resolution:** reconnect tracks the last external run whose completed history it refreshed. A previously unseen terminal run triggers refresh even without an observed running snapshot. Repeated terminal snapshots do not refresh repeatedly; the existing infinite cursor for a UI-owned POST stream continues to avoid duplicate handling.

**Regression evidence:** the public browser test holds live-run responses at idle while a real AgentClient `run_flow` job completes, then releases polling to the real terminal snapshot. It failed before the fix because no history entry appeared, then passed after the fix. It verifies the displayed entry's real run identity and observes three more live polls without another history read. The poll wait is bounded.

### Specification

**Original finding, P1:** validation and execution workers inherited the agent's complete ambient environment, including credentials and Python import overrides. Development commands already used a minimal environment, so the local permission boundary did not consistently apply to project processes.

**Resolution:** both paths now construct their minimal base environment through one shared function. Flow workers additionally receive only explicit operator-owned `inference_environment` configuration. Clients cannot provide environment overrides, and generic development commands do not receive the inference configuration. Startup rejects malformed values and overrides of PATH, Python, and dynamic-loader settings. Configured interpreter paths remain intact, including virtual-environment symlinks; worker imports resolve through the SDK installed normally in that environment.

**Regression evidence:** a real AgentClient validation previously failed on an assertion that an ambient secret was inherited. The regression now verifies successful validation and execution with an ambient secret, an injected PYTHONPATH module, and an invalid ambient PYTHONHOME excluded; an explicitly configured model location is available; the selected interpreter path is preserved; client-supplied environment configuration is rejected; and generic command output contains none of the inference or ambient values. No private worker or job methods are mocked.

## Additional gate repair

The pre-review combined gate exposed a test helper reusing `write-helper.py` for two different edits. The existing UI test reproduced the expected `request_conflict` rejection. Each new logical save now receives a fresh request identity; the same test passes without changing durable deduplication behavior. Persistence tests still verify duplicate requests, lost acknowledgements, concurrent retries, restart, and changed-content rejection. The optional MCP test already imports its remote fixture through a module alias at this integration tip, so no duplicate-discovery change was needed.

## Verification

The configured interpreter was `/tmp/phyzflow-edge08-venv/bin/python` (Python 3.12, Playwright and the optional MCP extra installed). The current worktree SDK was installed as a normal wheel with:

```sh
/tmp/phyzflow-edge08-venv/bin/python -m pip install --no-deps ./workspace
```

An initial `--no-build-isolation` attempt reported missing setuptools in the verification environment; the normal isolated wheel build/install succeeded. Test controllers and UI processes used the absolute worktree source path; scrubbed project workers used the normal installed SDK, never an inherited PYTHONPATH. From the review worktree:

```sh
export PYTHONPATH="$PWD/workspace/src:$PWD/workspace-test:$PWD/workspace-test/tests"
/tmp/phyzflow-edge08-venv/bin/python -m unittest \
  test_agent_jobs test_agent_commands test_agent_edit_ui test_agent_persistence -v
```

This focused run executed 25 tests: 24 passed; the existing command-retention test had one transient child exit code 1, with no stderr captured by its original assertion. The exact isolated test then passed all 32 launches, and passed again after adding failure diagnostics. No production change was inferred from that transient failure. Its assertion now includes the failed job and retained stderr to make any recurrence diagnosable, without retries or weakened expectations.

The two new regressions were rerun after strengthening the ambient-PYTHONHOME check and bounding the browser poll observation; both passed. The existing UI edit recovery test independently demonstrated red then green. The focused suite's passing cases cover history privacy, successful output when history persistence fails, selected interpreters, source reload and browser drafts, command environment isolation/offline installation/output/cancellation, sheet coordination, durable identities, crash recovery, and process cleanup. `git diff --check` passed. The parent integration task will run the final combined gate after merging this branch.

## Remaining limits

No new scope or deployment claim is added. Flow/command jobs still require Linux or macOS, SDK/dependencies must be installed in every configured environment, inference configuration changes require agent restart, and authorized project code runs with the agent account's operating-system permissions. Linux/systemd boot-manager validation still awaits an actual Linux target. Existing MCP stdio/TLS routing, history privacy defaults, durable output retention bounds, and the 32-command lifetime cap remain documented in [the agent guide](../../docs/workspace-agent.md).
