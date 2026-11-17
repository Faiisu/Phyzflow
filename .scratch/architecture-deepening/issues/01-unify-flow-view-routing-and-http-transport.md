# 01: Unify Flow View routing and HTTP transport across single-flow and Workspace servers

**What to build:** Serve and forward every Flow View endpoint through a single shared route definition and HTTP transport base so that new dashboard, source-editing, and inspection endpoints work identically in both single-flow and multi-sheet Workspace modes without route drift.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] A single canonical Flow View route definition validates both GET and POST paths across single-flow and Workspace proxy servers.
- [x] Local host checks, Origin checks, 1 MiB JSON object parsing, and source-edit error mapping share one HTTP transport implementation.
- [x] All GET and POST routes (including dashboard panels, dashboard assets, node files, node sources, history, and run) match parity tests across both servers.
