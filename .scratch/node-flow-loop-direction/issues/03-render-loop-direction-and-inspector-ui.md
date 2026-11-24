# 03: Render loop direction and inspector UI

**What to build:** Update `edge-routing.js`, `graph.js`, `inspector.js`, `display.js`, `run-controls.js`, `dashboard-ui.js`, and `app.css` to route and style loop edges with directional arrowheads and expose loop connections and iteration counts in the UI.

**Blocked by:** 01, 02

**Status:** resolved

- [x] Compute loop-edge routing in `edge-routing.js` for self-loops and multi-node back-edges, sweeping around spanned cards without crossing any card interior and stacking multiple loop lanes cleanly.
- [x] Render loop edges in `graph.js` with dashed strokes, `--edge-loop` styling, a mid-path directional arrowhead, and target-port arrowheads.
- [x] Show loop bindings in edge labels (`data-loop="true"`), update `#graph-summary`, display loop inputs/feeds in Inspector, and display iteration counts in node cards and Inspector Execution tab.

## Verification

Verified in Chrome via `LoopGraphBrowserTests` in `workspace-test/tests/test_loop_direction.py` (multi-node loop lane painting, zero card intersections, self-loop rendering, Inspector loop navigation, node `3×` badge, and Execution tab `Iterations`).
