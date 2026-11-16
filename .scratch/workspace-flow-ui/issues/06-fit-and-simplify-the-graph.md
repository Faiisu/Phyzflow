# 06: Fit the execution graph and simplify node cards

**What to build:** Fit the whole execution graph or the selected node on demand. Read node names, status, and duration at a glance, and use the Inspector when the full source location is needed.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] Fit graph adjusts zoom and pan to frame the graph. Fit selection frames the selected node. These actions run only when the user invokes them.
- [x] Node cards prioritize name, status, and duration. The full source path and line are available in the Inspector, and double-click still opens node files.
- [x] Execution graph remains the full-page canvas. Pan, wheel zoom, and independent panel visibility continue to work.
- [x] A browser test uses both fit actions on the Telemetry graph and confirms that node selection and source access still work.

## Verification

The Chrome test verified Fit graph and Fit selection on the Telemetry graph at desktop and narrow widths and opened node source from the fitted view. Existing pan, wheel zoom, and panel interactions also passed with the installed SDK wheel.
