export const WORKSPACE_STEP = 20;
export const DEFAULT_PANEL_WIDTH = 600;
export const DEFAULT_PANEL_HEIGHT = 400;

export function snapStep(value) {
  return Math.round(value / WORKSPACE_STEP) * WORKSPACE_STEP;
}

export function snapDashboardSize(value) {
  return Math.max(0, snapStep(value));
}

export function createCanvasLayout({
  graph,
  graphPlane,
  graphViewport,
  nodes,
  getZoom,
  graphAreas,
  graphAnchor,
  graphBounds,
  frameGraphBounds,
  layoutGraph,
  layoutSize,
  cancelWheelZoom,
}) {
  const savedCanvasLayouts = new Map();
  const savedVisualizeLayouts = new Map();
  const panelKeys = new WeakMap();
  let boardDrag = null;

  function isVisualizeMode() {
    return document.body.classList.contains("is-visualize-mode");
  }

  function activeLayoutMap(visualize = isVisualizeMode()) {
    return visualize ? savedVisualizeLayouts : savedCanvasLayouts;
  }

  function visibleDashboards() {
    return [...graph.querySelectorAll(".dashboard-panel:not([hidden])")].filter(
      panel => document.fullscreenElement !== panel,
    );
  }

  function focusDashboard(panel) {
    for (const item of graph.querySelectorAll(".dashboard-panel")) {
      item.classList.toggle("is-focused", item === panel);
    }
  }

  function saveBounds(panel, key = panelKeys.get(panel), map = activeLayoutMap()) {
    if (!key || !panel.style.left) return;
    map.set(key, {
      left: panel.style.left,
      top: panel.style.top,
      width: panel.style.width,
      height: panel.style.height,
    });
  }

  function updateVisualizeScrollHeight() {
    if (!isVisualizeMode()) return;
    const minHeight = graphViewport.clientHeight || window.innerHeight;
    let maxBottom = minHeight;
    for (const panel of visibleDashboards()) {
      const top = parseFloat(panel.style.top) || 0;
      const height = panel.offsetHeight || parseFloat(panel.style.height) || DEFAULT_PANEL_HEIGHT;
      maxBottom = Math.max(maxBottom, top + height + WORKSPACE_STEP * 2);
    }
    const px = `${Math.ceil(maxBottom)}px`;
    graph.style.height = px;
    if (graphPlane) graphPlane.style.height = px;
  }

  function releaseDashboard(panel, key = panelKeys.get(panel)) {
    saveBounds(panel, key);
    panel.style.left = "";
    panel.style.top = "";
    panel.style.width = "";
    panel.style.height = "";
    updateVisualizeScrollHeight();
  }

  function dashboardBounds(panel) {
    return {
      left: parseFloat(panel.style.left) || 0,
      top: parseFloat(panel.style.top) || 0,
      ...layoutSize(panel),
    };
  }

  function resolveVisualizeCollisions(priorityPanel = null) {
    if (!isVisualizeMode()) return;
    const gap = WORKSPACE_STEP;
    const panels = visibleDashboards().filter(item => item.style.left);
    const others = panels
      .filter(item => item !== priorityPanel)
      .sort((a, b) => (parseFloat(a.style.top) - parseFloat(b.style.top)) || (parseFloat(a.style.left) - parseFloat(b.style.left)));
    const ordered = priorityPanel && priorityPanel.style.left ? [priorityPanel, ...others] : others;
    const placed = [];

    for (const item of ordered) {
      const box = {
        left: Math.max(gap, snapStep(parseFloat(item.style.left) || gap)),
        top: Math.max(gap, snapStep(parseFloat(item.style.top) || gap)),
        width: item.offsetWidth || parseFloat(item.style.width) || DEFAULT_PANEL_WIDTH,
        height: item.offsetHeight || parseFloat(item.style.height) || DEFAULT_PANEL_HEIGHT,
      };
      if (item !== priorityPanel) {
        let guard = 0;
        while (guard++ < 64) {
          const blocker = placed.find(p =>
            box.left < p.left + p.width + gap &&
            box.left + box.width + gap > p.left &&
            box.top < p.top + p.height + gap &&
            box.top + box.height + gap > p.top
          );
          if (!blocker) break;
          box.top = snapStep(blocker.top + blocker.height + gap);
        }
        item.style.left = `${box.left}px`;
        item.style.top = `${box.top}px`;
      }
      placed.push(box);
      saveBounds(item, panelKeys.get(item), savedVisualizeLayouts);
    }
  }

  function changeDashboardBounds(panel, bounds, dx, dy, resize) {
    const visualize = isVisualizeMode();
    const gap = WORKSPACE_STEP;
    const viewportWidth = Math.max(640, graphViewport.clientWidth || window.innerWidth);
    if (resize) {
      let width = Math.max(280, snapDashboardSize(bounds.width + dx));
      const height = Math.max(220, snapDashboardSize(bounds.height + dy));
      if (visualize) {
        const left = parseFloat(panel.style.left) || gap;
        width = Math.min(width, Math.max(280, snapDashboardSize(viewportWidth - left - gap)));
      }
      panel.style.width = `${width}px`;
      panel.style.height = `${height}px`;
      panel.dataset.resizeSize = `${Math.round(panel.offsetWidth || width)} × ${Math.round(panel.offsetHeight || height)}`;
    } else {
      let nextLeft = snapStep(bounds.left + dx);
      let nextTop = snapStep(bounds.top + dy);
      if (visualize) {
        const width = panel.offsetWidth || parseFloat(panel.style.width) || DEFAULT_PANEL_WIDTH;
        const maxLeft = Math.max(gap, snapStep(viewportWidth - width - gap));
        nextLeft = Math.max(gap, Math.min(maxLeft, nextLeft));
        nextTop = Math.max(gap, nextTop);
      }
      panel.style.left = `${nextLeft}px`;
      panel.style.top = `${nextTop}px`;
    }
    if (visualize) {
      if (boardDrag?.otherBounds) {
        for (const [other, orig] of boardDrag.otherBounds) {
          other.style.left = `${orig.left}px`;
          other.style.top = `${orig.top}px`;
        }
      }
      resolveVisualizeCollisions(panel);
      updateVisualizeScrollHeight();
    }
  }

  function ensureHeaderVisible(panel, areas, viewport) {
    if (document.fullscreenElement === panel || isVisualizeMode()) return;
    const handle = panel.querySelector(".dock-heading");
    const header = handle.getBoundingClientRect();
    const visible = areas.some(a => header.top >= a.top + viewport.top &&
      header.bottom <= a.bottom + viewport.top &&
      Math.min(header.right, a.right + viewport.left) - Math.max(header.left, a.left + viewport.left) >= Math.min(160, header.width));
    if (!visible) frameGraphBounds(graphBounds([handle]));
  }

  function findNonOverlappingSlot(panel, width, height, startLeft, startTop, maxRowWidth, gap) {
    const others = visibleDashboards()
      .filter(other => other !== panel && other.style.left)
      .map(other => ({
        left: parseFloat(other.style.left),
        top: parseFloat(other.style.top),
        width: other.offsetWidth || parseFloat(other.style.width) || width,
        height: other.offsetHeight || parseFloat(other.style.height) || height,
      }));

    const overlaps = (lx, ty) => others.some(box =>
      lx < box.left + box.width + gap &&
      lx + width + gap > box.left &&
      ty < box.top + box.height + gap &&
      ty + height + gap > box.top
    );

    let left = startLeft;
    let top = startTop;
    if (others.length) {
      let guard = 0;
      while (overlaps(left, top) && guard++ < 64) {
        const blocker = others.find(box =>
          left < box.left + box.width + gap &&
          left + width + gap > box.left &&
          top < box.top + box.height + gap &&
          top + height + gap > box.top
        );
        const nextLeft = snapStep(blocker.left + blocker.width + gap);
        if (nextLeft + width - startLeft <= maxRowWidth) {
          left = nextLeft;
        } else {
          left = startLeft;
          top = snapStep(blocker.top + blocker.height + gap);
        }
      }
    }
    return { left: snapStep(left), top: snapStep(top) };
  }

  function placeInVisualizeMode(panel, key) {
    const layouts = savedVisualizeLayouts;
    if (key && layouts.has(key)) {
      const saved = layouts.get(key);
      panel.style.left = saved.left;
      panel.style.top = saved.top;
      panel.style.width = saved.width;
      panel.style.height = saved.height;
      resolveVisualizeCollisions(panel);
      updateVisualizeScrollHeight();
      return;
    }

    const viewportWidth = Math.max(640, graphViewport.clientWidth || window.innerWidth);
    const gap = WORKSPACE_STEP;
    const colWidth = Math.max(360, snapDashboardSize((viewportWidth - gap * 3) / 2));
    const width = Math.min(colWidth, snapDashboardSize(viewportWidth - gap * 2));
    const height = DEFAULT_PANEL_HEIGHT;
    const slot = findNonOverlappingSlot(panel, width, height, gap, gap, viewportWidth - gap * 2, gap);

    panel.style.width = `${width}px`;
    panel.style.height = `${height}px`;
    panel.style.left = `${Math.max(gap, slot.left)}px`;
    panel.style.top = `${Math.max(gap, slot.top)}px`;
    resolveVisualizeCollisions(panel);
    saveBounds(panel, key, layouts);
    updateVisualizeScrollHeight();
  }

  function revealDashboard(panel, key) {
    focusDashboard(panel);
    if (key) panelKeys.set(panel, key);

    if (isVisualizeMode()) {
      if (!panel.style.left) placeInVisualizeMode(panel, key);
      else updateVisualizeScrollHeight();
      return;
    }

    const areas = graphAreas();
    const viewport = graphViewport.getBoundingClientRect();

    if (!panel.style.left && key && savedCanvasLayouts.has(key)) {
      const saved = savedCanvasLayouts.get(key);
      panel.style.left = saved.left;
      panel.style.top = saved.top;
      panel.style.width = saved.width;
      panel.style.height = saved.height;
      layoutGraph();
      ensureHeaderVisible(panel, areas, viewport);
      return;
    }

    if (!panel.style.left) {
      const margin = 12;
      const room = area => Math.min(
        (area.right - area.left - 2 * margin) / DEFAULT_PANEL_WIDTH,
        (area.bottom - area.top - 2 * margin) / DEFAULT_PANEL_HEIGHT,
      );
      let candidates = areas;
      if (nodes.size) {
        const rectangles = Array.from(nodes.values(), ({ node }) => node.getBoundingClientRect());
        const top = Math.min(...rectangles.map(rect => rect.top)) - viewport.top;
        const bottom = Math.max(...rectangles.map(rect => rect.bottom)) - viewport.top;
        const clearAreas = areas.flatMap(area => [
          { ...area, bottom: Math.min(area.bottom, top) },
          { ...area, top: Math.max(area.top, bottom) },
        ]).filter(area => room(area) >= .6);
        if (clearAreas.length) candidates = clearAreas;
      }
      const area = candidates.reduce((best, next) => room(next) > room(best) ? next : best);
      const x = (area.left + area.right) / 2;
      const y = (area.top + area.bottom) / 2;
      const zoom = getZoom();
      const ratio = 100 / zoom;
      const width = Math.round(Math.max(280, Math.min(DEFAULT_PANEL_WIDTH, area.right - area.left - 2 * margin)) * ratio / WORKSPACE_STEP) * WORKSPACE_STEP;
      const height = Math.round(Math.max(220, Math.min(DEFAULT_PANEL_HEIGHT, area.bottom - area.top - 2 * margin)) * ratio / WORKSPACE_STEP) * WORKSPACE_STEP;
      const { graphX, graphY } = graphAnchor(x, y);
      const gap = 20 * ratio;

      const others = visibleDashboards().filter(other => other !== panel && other.style.left);
      const baseLeft = others.length
        ? Math.min(...others.map(other => parseFloat(other.style.left)))
        : snapStep(graphX - width / 2);
      const baseTop = others.length
        ? Math.min(...others.map(other => parseFloat(other.style.top)))
        : snapStep(graphY - height / 2);
      const maxRowWidth = Math.max(width * 2 + gap * 2, (area.right - area.left) * ratio);
      const slot = findNonOverlappingSlot(panel, width, height, baseLeft, baseTop, maxRowWidth, gap);

      panel.style.width = `${width}px`;
      panel.style.height = `${height}px`;
      panel.style.left = `${slot.left}px`;
      panel.style.top = `${slot.top}px`;
      saveBounds(panel, key, savedCanvasLayouts);
      layoutGraph();
    } else {
      ensureHeaderVisible(panel, areas, viewport);
    }
  }

  function setVisualizeModeLayout(active) {
    const prevMap = activeLayoutMap(!active);
    const panels = visibleDashboards();
    for (const panel of panels) {
      saveBounds(panel, panelKeys.get(panel), prevMap);
      panel.style.left = "";
      panel.style.top = "";
      panel.style.width = "";
      panel.style.height = "";
    }
    if (!active) {
      graph.style.height = "";
      if (graphPlane) graphPlane.style.height = "";
    } else {
      graphViewport.scrollLeft = 0;
      graphViewport.scrollTop = 0;
    }
    for (const panel of panels) {
      const key = panelKeys.get(panel);
      if (active) placeInVisualizeMode(panel, key);
      else revealDashboard(panel, key);
    }
    if (!active) layoutGraph();
  }

  graph.addEventListener("pointerdown", event => {
    const panel = event.target.closest(".dashboard-panel");
    if (!panel) return;
    focusDashboard(panel);
    if (boardDrag || !event.isPrimary || event.button !== 0 || document.fullscreenElement === panel) return;
    const resizeHandle = panel.querySelector('[data-dash="resize"]');
    const handle = panel.querySelector(".dock-heading");
    const resize = event.target === resizeHandle;
    if (!resize && (!handle.contains(event.target) || event.target.closest("button"))) return;
    cancelWheelZoom();
    const otherBounds = isVisualizeMode()
      ? new Map(
        visibleDashboards()
          .filter(other => other !== panel && other.style.left)
          .map(other => [other, { left: parseFloat(other.style.left), top: parseFloat(other.style.top) }]),
      )
      : null;
    boardDrag = {
      panel, id: event.pointerId, resize, x: event.clientX, y: event.clientY,
      scrollLeft: graphViewport.scrollLeft, scrollTop: graphViewport.scrollTop, bounds: dashboardBounds(panel),
      otherBounds,
    };
    panel.setPointerCapture(event.pointerId);
    panel.classList.add(resize ? "is-resizing" : "is-moving");
    graphViewport.classList.add("is-dragging-dashboard");
    event.preventDefault();
  });

  graph.addEventListener("pointermove", event => {
    if (boardDrag?.id !== event.pointerId) return;
    const ratio = isVisualizeMode() ? 1 : 100 / getZoom();
    const dx = event.clientX - boardDrag.x + graphViewport.scrollLeft - boardDrag.scrollLeft;
    const dy = event.clientY - boardDrag.y + graphViewport.scrollTop - boardDrag.scrollTop;
    changeDashboardBounds(boardDrag.panel, boardDrag.bounds, dx * ratio, dy * ratio, boardDrag.resize);
  });

  graph.addEventListener("lostpointercapture", event => {
    if (boardDrag?.id !== event.pointerId) return;
    const { panel } = boardDrag;
    boardDrag = null;
    panel.classList.remove("is-resizing", "is-moving");
    delete panel.dataset.resizeSize;
    graphViewport.classList.remove("is-dragging-dashboard");
    if (isVisualizeMode()) {
      for (const item of visibleDashboards()) saveBounds(item, panelKeys.get(item), savedVisualizeLayouts);
      updateVisualizeScrollHeight();
    } else {
      saveBounds(panel);
      layoutGraph();
    }
  });

  graph.addEventListener("keydown", event => {
    const panel = event.target.closest(".dashboard-panel");
    if (!panel) return;
    const handle = panel.querySelector(".dock-heading");
    if (event.target !== handle || document.fullscreenElement === panel) return;
    const direction = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] }[event.key];
    if (!direction) return;
    event.preventDefault();
    cancelWheelZoom();
    changeDashboardBounds(panel, dashboardBounds(panel), direction[0] * WORKSPACE_STEP, direction[1] * WORKSPACE_STEP, event.shiftKey);
    if (isVisualizeMode()) {
      for (const item of visibleDashboards()) saveBounds(item, panelKeys.get(item), savedVisualizeLayouts);
      updateVisualizeScrollHeight();
    } else {
      saveBounds(panel);
      layoutGraph();
    }
  });

  return { visibleDashboards, revealDashboard, releaseDashboard, setVisualizeModeLayout, updateVisualizeScrollHeight };
}
