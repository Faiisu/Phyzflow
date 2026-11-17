# 01: Edit and save a sheet's flow source

**What to build:** Open a sheet's `flow.py` directly from the Workspace, edit its Python source, and save it to the actual project file. Apply the saved changes with Reload flow.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] Open the source editor for a runnable sheet even when all Nodes are defined in separate files.
- [x] Save a valid edit and verify that the new text is present on disk and visible to Git or an external editor. After Reload flow, the graph and the next run reflect the saved changes in the same Workspace session.
- [x] If the file changed on disk after it was opened, Save reports the conflict, preserves the browser draft, and leaves the newer file intact.
- [x] Verify the save, reload, run, and conflict cases through the browser on a temporary project.

## Verification

Browser tests verified the edited file on disk, the updated graph and run output, and conflict handling after changes from another editor. Save rejects encoding declarations that would prevent the draft from being read back intact.
