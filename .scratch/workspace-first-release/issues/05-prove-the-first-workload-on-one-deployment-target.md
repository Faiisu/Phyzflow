# 05: Prove the first workload on one deployment target

**What to build:** Prove that the first real-model workload can accept a representative request on one chosen deployment target before implementing the deploy command.

**Blocked by:** 01 (Run the sample from a clean installation)

**Status:** ready-for-agent

- [ ] A build installed in a clean target environment passes `workspace check` and returns the expected response through the serving path using the real model and representative dependencies.
- [ ] The trial uses any native or GPU dependency required by the workload.
- [ ] Repeatable commands document the build and request, including the target Python version, dependencies, model loading, and request and response format.
- [ ] Any compatibility issue that prevents the trial from passing is resolved and retested on the chosen target before deployment proceeds.
