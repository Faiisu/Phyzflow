# 01: Report module changes by release

**What to build:** Let an operator identify an installed Phyzflow build and see which release last changed each module shipped in its wheel.

**Blocked by:** None (can start immediately).

Status: resolved

- [x] The build generates a manifest from the packaged files for the Application, CLI, Runner, Web UI, and MCP modules. It records the release version, Git commit, and a reproducible fingerprint per module.
- [x] The manifest compares each fingerprint with the previous release manifest and records the latest release that changed that module. The first available manifest establishes a baseline with no earlier history inferred.
- [x] After installing the wheel in a clean environment, `workspace version --json` reports the manifest for that installation.
- [x] Build and install two test releases that differ in the packaged files of one module. Verify that only that module's fingerprint and last-changed release advance.

## Verification

Two installed wheels identified the changed module while retaining the last-changed release for the other modules. Sequential macOS releases also carried forward the previous manifest.
