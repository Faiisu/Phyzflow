# 02: Create and edit flow source through the agent

**What to build:** A client creates and patches Python flow, node, and dashboard files in the selected project and a person can inspect the resulting files in Workspace.

**Blocked by:** 01: Inspect an Edge workspace through a local agent.

**Status:** resolved

- [x] Create a new source file and apply an edit using an expected revision; report the resulting revision and a reviewable change summary.
- [x] Reject stale revisions and invalid or out-of-project targets without modifying existing content; a failed edit leaves the original file intact.
- [x] Exercise an edit made concurrently from another editor and verify that the agent reports a conflict instead of overwriting it.
- [x] The existing Web UI can inspect edited node source and the resulting graph after reload; validation errors remain recoverable through source editing.
- [x] Verify that active runs and unsaved browser source drafts are preserved when agent edits trigger existing reload behavior.
- [x] Use existing source-editing behavior through shared operations and cover creation, patching, conflicts, and denied writes through the agent entry point.

## Answer

Added `write_file` and `apply_patch` through the authenticated local agent contract. Both require expected revisions and use the shared atomic source editor. New files require revision `missing`; literal patches require exactly one match. The response includes a new revision and bounded reviewable unified diff. Source writes reject hidden metadata, virtual-environment paths, caches, symbolic links, unsupported extensions, and escapes outside the project. Existing Python encodings and newline conventions are preserved.

TDD evidence: the first create/patch client test failed with `unsupported_operation` before implementation. An unknown Python encoding test exposed a disconnected request; the agent now reports a structured error and preserves the file.

Verification through public client and browser seams:

- Agent edit tests: 4 passed (create/patch, stale and denied writes, legacy encoding/newlines, invalid encoding).
- Browser integration tests: 3 passed (graph/source reload and syntax-error recovery, unsaved draft preservation, active-run reload deferral and subsequent run output).
- Existing local agent inspection tests: 6 passed.
- Existing automatic reload tests: 4 passed.
- `git diff --check`: passed.

Run with the project SDK environment and the checkout's source plus consumer directory on `PYTHONPATH`, using unittest discovery for `test_agent_edit.py`, `test_agent_edit_ui.py`, `test_agent.py`, and `test_auto_reload.py`. Request size remains bounded by the existing transport; durable mutation deduplication is assigned to ticket 05.
