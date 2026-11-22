# 03. Render large graph edges on high-DPI displays

Status: resolved

In Chrome at a 1440 by 900 viewport, a 150-node chain paints edges at device pixel ratio 1. At ratio 2, its 74,940 by 1,524 Canvas loses the edge pixels while the cards remain visible.

The edge renderer sizes the backing bitmap from the full unzoomed graph multiplied by device pixel ratio. Fit scales the displayed graph but leaves the backing dimensions unchanged.

- [x] Edges stay visible in the 150-node graph at device pixel ratio 2 after opening and using Fit graph.
- [x] Edge drawing remains aligned with cards when panning, zooming, and dragging nodes.
- [x] Ordinary graphs retain their edge styling and interaction.

## Verification

Chrome rendered edge pixels on a 150-node chain at device pixel ratio 2 after opening, fitting, zooming, panning, and dragging. Existing coverage for dependency selection, binding labels, and desktop and mobile fit passed.
