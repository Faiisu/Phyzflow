# 05: Gate releases on tested compatibility

**What to build:** Give release maintainers a reproducible compatibility report for the exact artifact that will be published and prevent publication when an advertised combination fails verification.

**Blocked by:** 01: Report module changes by release, 02: Validate Workspace format version, 03: Verify Application and Sheet Worker compatibility, and 04: Verify Dashboard Panel and Dashboard Bridge compatibility.

Status: resolved

- [x] The release check installs the candidate wheel and tests the declared compatibility combinations for Workspace format, Application and Sheet Worker protocol, and Dashboard Panel and Dashboard Bridge interface.
- [x] Representative unsupported combinations are rejected with the expected diagnostic. Compatible combinations complete their normal operation.
- [x] The release report records the release and commit, artifact checksum, each tested combination, and its result. The published record includes the actual publication time.
- [x] A failed or untested advertised combination causes the release check to exit unsuccessfully and prevents publication through the release path.
- [x] Demonstrate a passing release check and a deliberately failing advertised combination against candidate artifacts.

## Verification

The installed macOS release candidate passed all 12 compatibility combinations and 17 test results. The signed ZIP, wheel, and publication report had matching checksums. Candidate tests verified that failed and untested advertised combinations block release.
