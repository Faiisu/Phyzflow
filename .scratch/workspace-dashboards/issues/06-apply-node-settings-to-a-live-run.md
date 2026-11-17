# 06: Apply node settings to a live run

Status: resolved

**What to build:** Change an opted-in setting during a run and see when the node applies it.

**Blocked by:** 03: Follow a live run in dashboards, and 05: Save node settings for the next run.

- [x] A dashboard distinguishes a queued change from a change acknowledged by the node at an explicit checkpoint.
- [x] Telemetry Control Room shows the effect of a change on subsequent work during the run without persisting the live override.
- [x] Stale run IDs, inactive nodes and settings without live support are rejected. A change left pending when its node ends reaches a terminal result.

## Verification

Chrome showed the applied acknowledgement with the run ID and update time, and later work used the new submission limit. The persisted value remained 3. The Runner test covered queued commands, checkpoint application, rejection at node completion, duplicate pending commands, and inactive runs.
