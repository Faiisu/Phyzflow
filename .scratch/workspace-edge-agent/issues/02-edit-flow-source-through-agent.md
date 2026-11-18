# 02: Create and edit flow source through the agent

**What to build:** A client creates and patches Python flow, node, and dashboard files in the selected project and a person can inspect the resulting files in Workspace.

**Blocked by:** 01: Inspect an Edge workspace through a local agent.

**Status:** ready-for-agent

- [ ] Create a new source file and apply an edit using an expected revision; report the resulting revision and a reviewable change summary.
- [ ] Reject stale revisions and invalid or out-of-project targets without modifying existing content; a failed edit leaves the original file intact.
- [ ] Exercise an edit made concurrently from another editor and verify that the agent reports a conflict instead of overwriting it.
- [ ] The existing Web UI can inspect edited node source and the resulting graph after reload; validation errors remain recoverable through source editing.
- [ ] Verify that active runs and unsaved browser source drafts are preserved when agent edits trigger existing reload behavior.
- [ ] Use existing source-editing behavior through shared operations and cover creation, patching, conflicts, and denied writes through the agent entry point.
