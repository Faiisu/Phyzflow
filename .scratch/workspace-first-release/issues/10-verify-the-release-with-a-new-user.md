# 10: Verify the release with a new user

**What to build:** Prove that a new user can follow the repository instructions from installation through diagnosis and deployment, and that the finished release stays within the performance limits.

**Blocked by:** 06 (Deploy the validated workload and trace failures), 09 (Explain an incorrect result from node evidence)

**Status:** ready-for-agent

- [ ] A new user follows the documented steps to install in a fresh environment, add a node with a nested source file, and run the flow.
- [ ] The user locates a failing node and diagnoses an incorrect successful result from evidence in the UI, then deploys the real-model workload and verifies the expected response.
- [ ] Resolve observed workflow blockers and repeat the affected steps.
- [ ] Re-run the first workload benchmark using the original method. The added p95 latency and peak memory of both Runner modes stay within the established limits.
