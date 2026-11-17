# 03: Deepen sheet lifecycle, source-tree stamping, and flow entrypoint editing

**What to build:** Consolidate sheet discovery, starter flow creation, deletion archiving, working-tree source stamping for auto-reload, and entrypoint source editing into the sheet lifecycle module so the Workspace server supervises worker processes and revisions without filesystem traversal details.

**Blocked by:** 01: Unify Flow View routing and HTTP transport across single-flow and Workspace servers.

**Status:** resolved

- [x] Sheet discovery, starter creation, trash archiving, source-tree stamping, and entrypoint editing are exposed through the sheet lifecycle module interface.
- [x] Symlink protections, revision conflict detection, and exclusion of hidden/cache/nested-sheet directories are preserved.
- [x] The Workspace server delegates sheet filesystem operations across the sheet lifecycle seam while retaining worker process supervision.
