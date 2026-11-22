# 01. Keep the recovery graph and node files in sync

Status: resolved

Before the first successful load, open a sheet with invalid Python and a preview showing Start. Replace the declaration with a different broken node named Second and choose Reload flow. The old iframe can still display Start while its file action opens Second.

The Workspace retains an unversioned recovery iframe. The node-file handler recomputes the preview and resolves its numeric index against a potentially different graph. A file revision protects concurrent edits to the selected file but cannot detect that the visible node refers to a different file.

- [x] Reload refreshes the recovery preview while honoring the existing unsaved-draft behavior.
- [x] File reads and saves resolve against the graph displayed by the requesting frame, or reject invalidated requests explicitly.
- [x] Recovery retains the last loaded graph after restart and remains usable after repairs.

## Verification

The Chrome regression rejected stale file listings, reads, and saves with HTTP 409, preserved a draft when reload was canceled, refreshed the preview on confirmed reload, and verified repair, rerun, and recovery after restart.
