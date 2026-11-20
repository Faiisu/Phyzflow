# 01: Report module changes by release

**What to build:** Let an operator identify an installed Phyzflow build and see which release last changed each module shipped in its wheel.

**Blocked by:** None (can start immediately).

Status: ready-for-agent

- [ ] The build generates a manifest from the packaged files for the Application, CLI, Runner, Web UI, and MCP modules. It records the release version, Git commit, and a reproducible fingerprint per module.
- [ ] The manifest compares each fingerprint with the previous release manifest and records the latest release that changed that module. The first available manifest establishes a baseline with no earlier history inferred.
- [ ] After installing the wheel in a clean environment, `workspace version --json` reports the manifest for that installation.
- [ ] Build and install two test releases that differ in the packaged files of one module. Verify that only that module's fingerprint and last-changed release advance.
