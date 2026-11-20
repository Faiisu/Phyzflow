# 02: Validate Workspace format version

**What to build:** Let a project declare its Workspace structure version so Phyzflow can check whether it can load the Workspace and its sheets.

**Blocked by:** None (can start immediately).

Status: resolved

- [x] Creating a Workspace records `format_version = 1` in the root project's Workspace configuration.
- [x] CLI validation and the application use the same format rules. The application checks the root format before discovering sheets, and validation for a registered child sheet uses the root declaration.
- [x] A Workspace with an unsupported or malformed declaration receives a diagnostic identifying the configuration, the value found, and the supported version before flow code loads.
- [x] A Workspace with no declaration reports that the format is unspecified and follows existing structural validation without rewriting its configuration.
- [x] Verify supported, unsupported, malformed, and unspecified declarations through the normal CLI and application entry points.

## Verification

CLI and browser tests covered supported, unsupported, malformed, and unspecified declarations, including a mounted Workspace whose root format changed while a run was active.
