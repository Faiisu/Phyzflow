# 01: Automatically reload a sheet after source edits

**What to build:** When a developer or agent saves Python source in an open, idle sheet, briefly wait for the writes to settle and reload the affected sheet. Display the updated graph and use the saved code the next time the user runs it.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] A browser test changes the graph declaration and a Python helper on disk and observes one automatic reload after the edits settle.
- [x] The graph updates in the same Workspace session and the next user initiated run returns the new result.
- [x] Another open sheet retains its current view and source version.
