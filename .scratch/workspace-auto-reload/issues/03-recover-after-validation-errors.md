# 03: Recover automatically after a validation error

**What to build:** If the edited source fails validation, show the failure stage and source location when available. Retry automatically after another source change and let the user run the recovered sheet from the same Workspace.

**Blocked by:** 01: Automatically reload a sheet after source edits.

**Status:** resolved

- [x] A browser test introduces invalid Python and verifies the diagnostic. The invalid version is attempted once.
- [x] After the user corrects the source, the graph recovers automatically and the next run succeeds.
