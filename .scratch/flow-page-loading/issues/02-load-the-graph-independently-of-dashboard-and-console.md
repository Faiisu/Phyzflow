# 02. Load the graph independently of Dashboard and Console

Status: resolved

A valid Flow appears as Ready with a blank graph when a Dashboard panels directory cannot be read. A retained Console log of about 129 MiB also reproduced a 502 after two seconds.

The worker gathers the Dashboard catalog and reads, serializes, and potentially compresses the latest retained Console events inside the required project response. The Workspace proxy has a two-second deadline for upstream response headers. An unhandled catalog error or a slow log read prevents the frontend from initializing the graph.

- [x] A graph and its controls open when Dashboard discovery fails or Console loading stalls.
- [x] Available Dashboard panels and saved Console output still load through the regular Flow View.
- [x] Existing worker compatibility and Dashboard error isolation are preserved.

## Verification

Chrome opened the graph and enabled Run with a failed Dashboard scope and a pending Console response. A healthy Dashboard scope remained accessible through the batch endpoint and the per-scope fallback. Ordinary saved Console output and settings restored, and a late Console response did not replace fresh run output. With a 129.23 MiB retained log, the graph endpoint returned HTTP 200 and the editor was usable even though Console and live polling requests still timed out.
