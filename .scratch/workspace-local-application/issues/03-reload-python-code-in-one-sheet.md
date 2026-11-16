# 03: Reload Python code in one sheet

**What to build:** After an AI agent or developer saves Python changes, let a person reload the selected sheet and see its updated execution graph without restarting the Workspace.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] Reload flow reloads a selected sheet's Python source through normal project validation. The refreshed graph, source locations, and next run all use the updated code.
- [x] Reload preserves existing run history and does not restart another sheet or interrupt its active run.
- [x] If the selected sheet has an active run, the UI explains that reload is available once the run finishes or is stopped. The reload action does not terminate that run.
- [x] Invalid edited Python produces a diagnostic with source location when available. The user can correct the file and retry from the same Workspace session.
- [x] A browser test edits one sheet while a second sheet is running, reloads the first, and verifies the changed graph and output as well as successful completion of the second run.

## Verification

The browser test verified that reload refuses an active run, diagnoses invalid Python, and refreshes the graph, source file location, and output after correction. A second sheet's active run completed and earlier history remained available.
