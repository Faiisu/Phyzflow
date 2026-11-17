# 04: Consolidate project settings parsing and validation across CLI, UI, and deployment

**What to build:** Unify project settings file reading and schema validation for UI networking, zoom behavior, and Docker deployment under one project settings module so CLI, Web UI, and deployment commands enforce identical defaults and error diagnostics.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] One settings module owns reading the project settings file and validating both UI and deployment configuration schemas.
- [x] CLI, Web UI, and deployment paths share identical defaults, overrides, and validation error messages.
- [x] Existing deployment and UI settings tests pass without regression.
