# 04: Show metrics before a node finishes

Status: resolved

**What to build:** Let a node explicitly publish operational measurements while it is working.

**Blocked by:** 03: Follow a live run in dashboards.

- [x] Telemetry Control Room displays changing queue or throughput measurements before its pipeline node finishes.
- [x] Numeric snapshots are bounded. The saved run still contains the completed metrics, and the live samples stay ephemeral.

## Verification

The running Telemetry pipeline published a numeric submission limit of 9, then 3 after a live change. Both values appeared before completion. The saved run held completed CPU worker measurements and omitted the progress snapshots.
