# 02: Isolate canvas panel geometry, 20px grid snapping, and non-overlapping layout

**What to build:** Separate all canvas panel geometry—20px workspace grid snapping for nodes and panels, non-overlapping multi-panel initial placement at 600×400, step resizing with live dimension feedback, and saved per-panel bounds across Visualize open/close toggles—behind a narrow panel-identity interface decoupled from dashboard state management.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] Dashboard state management interacts with canvas layout strictly through panel identity without reading or resetting inline geometry properties.
- [x] Newly opened panels default to 600×400 (clamped and snapped to 20px steps) and avoid overlapping already-open panels on the canvas.
- [x] Both node dragging and dashboard panel move/resize snap cleanly to 20px steps, and panel bounds persist when toggled via Visualize.
