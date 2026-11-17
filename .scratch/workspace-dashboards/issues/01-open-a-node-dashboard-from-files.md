# 01: Open a node dashboard from files

Status: resolved

**What to build:** Open a node's Visualize action to load its own dashboard from the project and inspect a saved run.

**Blocked by:** None (can start immediately).

- [x] A node dashboard renders its own HTML, CSS and JavaScript inside the Flow Editor and reloads changed files on request.
- [x] Selecting a saved run provides the selected node's status, duration and metrics. A missing dashboard or run has an actionable empty state.
- [x] The isolated dashboard cannot read the parent page or files outside its scope. Traversal and escaping symlinks are rejected.

## Verification

Chrome rendered separate HTML, CSS, and JavaScript files, selected saved node data, and reloaded an edited file. Requests with invalid scopes or traversal paths failed. A symlink outside the dashboard directory was rejected. The dashboard could not read the parent document, fetch an external resource, or navigate itself to an external URL in the browser test.
