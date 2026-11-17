# 03: Follow a live run in dashboards

Status: resolved

**What to build:** Follow the current run in the existing workflow or node dashboard while keeping the same dashboard for saved runs.

**Blocked by:** 01: Open a node dashboard from files, and 02: Open a workflow dashboard from files.

- [x] Both scopes update from Runner status events and explicit output previews without reloading dashboard files.
- [x] Switching from a saved run to the current run shows current data with correctly scoped events.
- [x] Preview data is ephemeral. The dashboard channel omits request payloads and traceback text.

## Verification

The workflow overview followed a scoring failure live. The node dashboard showed active pipeline status and explicit progress previews, then switched back to the saved failure using the same files. The snapshot contained only the selected node and operational fields, and history omitted previews.
