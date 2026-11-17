# 02: Defer automatic reload during runs and unsaved edits

**What to build:** Indicate that the sheet has changes waiting to load while a run is active or a browser source editor has unsaved work. Reload the latest version once the run finishes and the draft is saved or discarded.

**Blocked by:** 01: Automatically reload a sheet after source edits.

**Status:** resolved

- [x] A browser test verifies a run completes with its loaded code before automatic reload applies the latest of successive saves.
- [x] Browser tests verify automatic reload leaves unsaved Flow and Node editor drafts intact, reports pending changes, and proceeds once the draft is resolved.
