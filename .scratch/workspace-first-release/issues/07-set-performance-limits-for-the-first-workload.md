# 07: Set performance limits for the first workload

**What to build:** Establish the first workload's performance baseline and overhead limits before adding run history.

**Blocked by:** 04 (Verify object passing and benchmark the demo), 05 (Prove the first workload on one deployment target)

**Status:** ready-for-agent

- [ ] Measure the proven workload directly, through the Runner alone, and through the Runner with the live UI using the documented demo benchmark method and equivalent inputs.
- [ ] Record p50 and p95 latency and peak memory after warm-up along with the environment and measurement scope.
- [ ] Set measurable limits for added p95 latency and peak memory for both Runner modes relative to direct execution so the release check can apply them unchanged.
