# 04: Verify object passing and benchmark the demo

**What to build:** Give developers a reproducible way to verify that the Runner preserves Python objects between nodes and to measure what the Workspace adds to the demo.

**Blocked by:** 03 (Run and debug the sample in the Web UI)

**Status:** resolved

- [x] A separate Runner test confirms that a downstream node receives the same CPU tensor object on the same device.
- [x] A documented procedure measures direct execution, execution through the Runner alone, and execution through the Runner with the live UI using the same inputs, machine, and Python environment.
- [x] The procedure records warm-up, p50 and p95 latency, peak memory, and the scope of processes and memory included in the measurement.

## Verification

The Runner test passed with a real PyTorch CPU tensor and checked object identity and device. Two runs of the documented three-mode benchmark used the same sample, environment, and machine. The sample project README records warm-up, p50 and p95 latency, peak RSS, and measurement scope. The complete acceptance suite passed.
