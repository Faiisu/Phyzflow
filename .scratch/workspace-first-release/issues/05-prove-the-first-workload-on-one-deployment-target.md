# 05: Prove the first workload on one deployment target

**What to build:** Prove that the first real-model workload can accept a representative request on one chosen deployment target before implementing the deploy command.

**Blocked by:** 01 (Run the sample from a clean installation)

**Status:** resolved

- [x] A build installed in a clean target environment passes `workspace check` and returns the expected response through the serving path using the real model and representative dependencies.
- [x] The trial uses any native or GPU dependency required by the workload.
- [x] Repeatable commands document the build and request, including the target Python version, dependencies, model loading, and request and response format.
- [x] Any compatibility issue that prevents the trial from passing is resolved and retested on the chosen target before deployment proceeds.

## Verification

Built the trial on the Colima Linux ARM64 Docker engine. The resulting CPython 3.12.16 image passed `workspace check` with networking disabled and loaded scikit-learn 1.7.2, NumPy 2.3.5, and SciPy 1.16.3. The serving smoke test passed three held-out examples and rejected a non-string input. See [the trial README](../../../workspace-test/deployment-trial/README.md) for the build and request commands.
