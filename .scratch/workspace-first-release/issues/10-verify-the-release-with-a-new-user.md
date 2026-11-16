# 10: Verify the release with a new user

**What to build:** Prove that a new user can follow the repository instructions from installation through diagnosis and deployment, and that the finished release stays within the performance limits.

**Blocked by:** 06 (Deploy the validated workload and trace failures), 09 (Explain an incorrect result from node evidence)

**Status:** ready-for-human

- [ ] A new user follows the documented steps to install in a fresh environment, add a node with a nested source file, and run the flow.
- [ ] The user locates a failing node and diagnoses an incorrect successful result from evidence in the UI, then deploys the real-model workload and verifies the expected response.
- [ ] Resolve observed workflow blockers and repeat the affected steps.
- [x] Re-run the first workload benchmark using the original method. The added p95 latency and peak memory of both Runner modes stay within the established limits.

## Automated release rehearsal

Tested the code at `db8c3fa` from a clean repository snapshot with a new Python environment. Followed the [local demo](../../../workspace-test/README.md#local-demo), installed the SDK and sample consumer from the repository, and verified `workspace check`, the Python API, and the CLI. Following [Add and debug a node](../../../workspace-test/README.md#add-and-debug-a-node), added Route in `ticket_demo/routing/queues.py`. The CLI and UI returned `queue: billing-urgent`. For the deliberate failure, the UI showed Route, `ValueError`, and `ticket_demo/routing/queues.py:3`.

The [evidence demo](../../../workspace-test/README.md#diagnose-an-incorrect-result) returned `billing` for a technical ticket. Predict's input sample, output sample, and fixed-category reason identified the first incorrect node. The [deployment walkthrough](../../../workspace-test/deployment-trial/README.md#deploy-and-verify) passed the image checks and smoke test on Colima Linux ARM64. The published endpoint returned HTTP 200 and the expected billing response. The forced failure correlated to its build, run, Predict node, and source file without exposing the synthetic request value in the logs. The test container was stopped.

Eight Workspace acceptance tests, two Docker integration tests, Pyright, and the JavaScript syntax check passed. Documentation review clarified the tool prerequisite, how to use the appropriate Docker context, and how to keep the node exercise separate from tests that expect the original sample flow. The documented deployment and log commands also worked with Colima selected through `DOCKER_CONTEXT`.

## Release benchmark

Reused the [Ticket 07 method and limits](../../../workspace-test/deployment-trial/README.md#benchmark-the-trained-workload) on Apple M2 with macOS 27.0.1 (arm64), CPython 3.12.11, Chrome 157.0.8103.51, Playwright 1.65.2, and the pinned model dependencies. Each mode used 20 warm-up requests followed by 100 timed requests with the same trained workload and expected result. Peak RSS covers the Python execution process, including startup and warm-up.

| Round | Mode | p50 (ms) | p95 (ms) | Peak RSS (MiB) |
| --- | --- | ---: | ---: | ---: |
| 1 | Direct | 0.048334 | 0.052708 | 98.812 |
| 1 | Runner | 0.071063 | 0.091875 | 98.859 |
| 1 | Runner with live UI | 1.6 | 2.4 | 97.406 |
| 2 | Direct | 0.047854 | 0.053709 | 98.797 |
| 2 | Runner | 0.068625 | 0.073834 | 98.828 |
| 2 | Runner with live UI | 1.7 | 2.3 | 99.469 |
| 3 | Direct | 0.048167 | 0.053125 | 98.844 |
| 3 | Runner | 0.068854 | 0.072875 | 98.719 |
| 3 | Runner with live UI | 1.6 | 2.5 | 99.641 |

Compared with direct execution in the same round, the largest increases were 0.039167 ms p95 and 0.047 MiB peak RSS for the Runner, and 2.446875 ms p95 and 0.797 MiB peak RSS for the live UI. Both modes stayed within both limits in every round.

## Pending first-time user walkthrough

A first-time human user still needs to follow the documented install, add Route, debug both UI cases, deploy, and verify the endpoint. Record where the user gets stuck, update the affected instructions or behavior, and repeat the affected steps before resolving the ticket.
