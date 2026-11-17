# 02: Trace generator failure to its producer

**What to build:** When a generator raises while another node reads from it, surface the failure on the producer and show how it affected the consumer. Let the user navigate from the error to the producer's Python source.

**Blocked by:** 01 (Track generator node completion).

**Status:** resolved

- [x] Run a producer that emits at least one value and then raises from a nested source file. The producer changes from Running to Failed, and the run ends with a failed status.
- [x] Run details let the user focus the producer and open the failing project file and line when available. The consumer reports the upstream failure, and nodes whose required result is unavailable show why they were skipped.
- [x] A browser test verifies failure attribution after a value was delivered. History reports the producer's failure and preserves the normal diagnostic privacy rules.

## Verification

Chrome verified a generator failure after a live value had been delivered, opened the producer's nested failure source, and displayed the consumer's failed dependency and the downstream skipped node. The saved run contained the producer's diagnostic location and excluded the exception payload.
