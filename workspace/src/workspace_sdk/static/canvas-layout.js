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
  const pendingBounds = new Map();
  const layoutStatus = document.querySelector("#visualize-layout-status");
  const retryLayout = layoutStatus.querySelector("button");
  const sharedLayout = /^\/workspaces\/[a-f0-9]{32}\/flows\//.test(location.pathname);
  let layoutLoad = sharedLayout ? "unloaded" : "ready";
  let layoutSave = "idle";
  let fitted = false;
  let boardDrag = null;

  function isVisualizeMode() {
    return document.body.classList.contains("is-visualize-mode");
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

  function saveCanvasBounds(panel, key = panelKeys.get(panel)) {
    if (!key || !panel.style.left) return;
    savedCanvasLayouts.set(key, {
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
    const visualize = isVisualizeMode();
    if (!visualize) saveCanvasBounds(panel, key);
    panel.style.left = "";
    panel.style.top = "";
    panel.style.width = "";
    panel.style.height = "";
    if (visualize) queueMicrotask(() => {
      if (isVisualizeMode()) renderVisualizeLayout();
    });
  }

  function dashboardBounds(panel) {
    return {
      left: parseFloat(panel.style.left) || 0,
      top: parseFloat(panel.style.top) || 0,
      ...layoutSize(panel),
    };
  }

  function orderedVisualizeBounds() {
    return visibleDashboards().filter(panel => panel.style.left)
      .map(panel => ({ panel, key: panelKeys.get(panel), ...readVisualizeBounds(panel) }))
      .sort((a, b) => a.top - b.top || a.left - b.left || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  }

  function resolveVisualizeCollisions(priorityPanel, boxes = orderedVisualizeBounds()) {
    const gap = WORKSPACE_STEP;
    const priority = boxes.find(box => box.panel === priorityPanel);
    const placed = priority ? [priority] : [];
    for (const box of boxes) {
      if (box === priority) continue;
      while (true) {
        const blocker = placed.find(p =>
          box.left < p.left + p.width + gap &&
          box.left + box.width + gap > p.left &&
          box.top < p.top + p.height + gap &&
          box.top + box.height + gap > p.top
        );
        if (!blocker) break;
        box.top = blocker.top + blocker.height + gap;
      }
      box.panel.style.top = `${box.top}px`;
      placed.push(box);
    }
  }

  function changeDashboardBounds(panel, bounds, dx, dy, resize) {
    const visualize = isVisualizeMode();
    const gap = WORKSPACE_STEP;
    const viewportWidth = graphViewport.clientWidth;
    if (resize) {
      let width = Math.max(280, snapDashboardSize(bounds.width + dx));
      const height = Math.max(220, snapDashboardSize(bounds.height + dy));
      if (visualize) {
        const left = parseFloat(panel.style.left) || gap;
        width = Math.min(width, Math.max(280, Math.floor((viewportWidth - left - gap) / gap) * gap));
      }
      panel.style.width = `${width}px`;
      panel.style.height = `${height}px`;
      panel.dataset.resizeSize = `${Math.round(panel.offsetWidth || width)} × ${Math.round(panel.offsetHeight || height)}`;
    } else {
      let nextLeft = snapStep(bounds.left + dx);
      let nextTop = snapStep(bounds.top + dy);
      if (visualize) {
        const width = panel.offsetWidth || parseFloat(panel.style.width) || DEFAULT_PANEL_WIDTH;
        const maxLeft = Math.max(gap, Math.floor((viewportWidth - width - gap) / gap) * gap);
        nextLeft = Math.max(gap, Math.min(maxLeft, nextLeft));
        nextTop = Math.max(gap, nextTop);
      }
      panel.style.left = `${nextLeft}px`;
      panel.style.top = `${nextTop}px`;
    }
    if (visualize) {
      if (boardDrag?.before) {
        for (const [other, orig] of boardDrag.before) {
          if (other === panel) continue;
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

  function defaultVisualizeBounds(panel) {
    const viewportWidth = Math.max(640, graphViewport.clientWidth || window.innerWidth);
    const gap = WORKSPACE_STEP;
    const colWidth = Math.max(360, snapDashboardSize((viewportWidth - gap * 3) / 2));
    const width = Math.min(colWidth, snapDashboardSize(viewportWidth - gap * 2));
    const height = DEFAULT_PANEL_HEIGHT;
    const slot = findNonOverlappingSlot(panel, width, height, gap, gap, viewportWidth - gap * 2, gap);

    return { width, height, left: Math.max(gap, slot.left), top: Math.max(gap, slot.top) };
  }

  function applyBounds(panel, bounds) {
    for (const [name, value] of Object.entries(bounds)) panel.style[name] = `${value}px`;
  }

  function readVisualizeBounds(panel) {
    const { left, top, width, height } = panel.style;
    return { left: parseInt(left, 10), top: parseInt(top, 10), width: parseInt(width, 10), height: parseInt(height, 10) };
  }

  function showLayoutStatus() {
    const messages = {
      loading: "Loading dashboard layout…",
      loadFailed: "Could not load the dashboard layout.",
      saving: "Saving dashboard layout…",
      saveFailed: "Could not save the dashboard layout.",
      fitted: "Layout editing is available in a wider window.",
    };
    const state = layoutLoad !== "ready" ? layoutLoad : layoutSave !== "idle" ? layoutSave : fitted ? "fitted" : "";
    const message = layoutStatus.querySelector('[role="status"]');
    if (message.textContent !== (messages[state] ?? "")) message.textContent = messages[state] ?? "";
    layoutStatus.hidden = !messages[state];
    retryLayout.hidden = state !== "loadFailed" && state !== "saveFailed";
    graphViewport.toggleAttribute("data-layout-locked", isVisualizeMode() && (fitted || layoutLoad !== "ready"));
    graphViewport.toggleAttribute("data-layout-loading", isVisualizeMode() && layoutLoad === "loading");
  }

  function renderVisualizeLayout() {
    if (boardDrag || layoutLoad === "loading") return;
    const panels = visibleDashboards();
    const unplaced = [];
    for (const panel of panels) {
      const saved = savedVisualizeLayouts.get(panelKeys.get(panel));
      if (saved) applyBounds(panel, saved);
      else {
        panel.style.left = "";
        unplaced.push(panel);
      }
    }
    for (const panel of unplaced) {
      const bounds = defaultVisualizeBounds(panel);
      savedVisualizeLayouts.set(panelKeys.get(panel), bounds);
      applyBounds(panel, bounds);
    }
    const ordered = orderedVisualizeBounds();
    const gap = WORKSPACE_STEP;
    const width = graphViewport.clientWidth;
    fitted = ordered.some(bounds => bounds.left + bounds.width + gap > width);
    graphViewport.toggleAttribute("data-layout-fitted", fitted);
    if (fitted) {
      let top = gap;
      const inset = Math.min(gap, Math.floor(width / 10));
      for (const bounds of ordered) {
        applyBounds(bounds.panel, {
          left: inset, top, width: Math.max(1, Math.min(bounds.width, width - inset * 2)), height: bounds.height,
        });
        top += bounds.height + gap;
      }
    } else {
      resolveVisualizeCollisions(null, ordered);
    }
    updateVisualizeScrollHeight();
    showLayoutStatus();
  }

  async function loadVisualizeLayout() {
    layoutLoad = "loading";
    showLayoutStatus();
    try {
      const response = await fetch("visualize-layout");
      if (!response.ok) throw new Error("Could not load dashboard layout.");
      const { panels } = await response.json();
      savedVisualizeLayouts.clear();
      for (const [key, bounds] of Object.entries(panels)) savedVisualizeLayouts.set(key, bounds);
      layoutLoad = "ready";
    } catch {
      layoutLoad = "loadFailed";
    }
    if (isVisualizeMode()) renderVisualizeLayout();
    else showLayoutStatus();
  }

  async function flushLayout() {
    if (layoutSave === "saving" || !pendingBounds.size) return;
    layoutSave = "saving";
    showLayoutStatus();
    try {
      while (pendingBounds.size) {
        const batch = new Map(pendingBounds);
        const body = JSON.stringify({ panels: Object.fromEntries(batch) });
        const response = await fetch("visualize-layout", {
          method: "POST", headers: { "Content-Type": "application/json" }, body,
          keepalive: new TextEncoder().encode(body).length <= 60 * 1024,
        });
        if (!response.ok) throw new Error("Could not save dashboard layout.");
        for (const [key, bounds] of batch) {
          if (pendingBounds.get(key) === bounds) pendingBounds.delete(key);
        }
      }
      layoutSave = "idle";
    } catch {
      layoutSave = "saveFailed";
    }
    showLayoutStatus();
  }

  function visibleVisualizeBounds() {
    return new Map(visibleDashboards().map(panel => [panel, readVisualizeBounds(panel)]));
  }

  function commitVisualizeBounds(before) {
    for (const [panel, bounds] of visibleVisualizeBounds()) {
      const original = before.get(panel);
      if (!original || Object.keys(bounds).every(name => bounds[name] === original[name])) continue;
      const key = panelKeys.get(panel);
      savedVisualizeLayouts.set(key, bounds);
      if (sharedLayout) pendingBounds.set(key, bounds);
    }
    if (layoutSave !== "saveFailed") void flushLayout();
  }

  retryLayout.addEventListener("click", () => {
    if (layoutLoad === "loadFailed") void loadVisualizeLayout();
    else if (layoutSave === "saveFailed") void flushLayout();
  });
  new ResizeObserver(() => {
    if (isVisualizeMode()) renderVisualizeLayout();
  }).observe(graphViewport);
  document.addEventListener("fullscreenchange", () => {
    if (isVisualizeMode()) renderVisualizeLayout();
  });

  function revealDashboard(panel, key) {
    focusDashboard(panel);
    if (key) panelKeys.set(panel, key);

    if (isVisualizeMode()) {
      renderVisualizeLayout();
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
      saveCanvasBounds(panel, key);
      layoutGraph();
    } else {
      ensureHeaderVisible(panel, areas, viewport);
    }
  }

  function setVisualizeModeLayout(active) {
    if (boardDrag) finishDashboardDrag(false);
    const panels = visibleDashboards();
    for (const panel of panels) {
      if (active) saveCanvasBounds(panel);
      panel.style.left = "";
      panel.style.top = "";
      panel.style.width = "";
      panel.style.height = "";
    }
    if (!active) {
      graph.style.height = "";
      if (graphPlane) graphPlane.style.height = "";
      graphViewport.removeAttribute("data-layout-fitted");
      for (const panel of panels) revealDashboard(panel, panelKeys.get(panel));
      layoutGraph();
      showLayoutStatus();
    } else {
      graphViewport.scrollLeft = 0;
      graphViewport.scrollTop = 0;
      if (sharedLayout && layoutLoad !== "loading" && !pendingBounds.size) void loadVisualizeLayout();
      else renderVisualizeLayout();
    }
  }

  graph.addEventListener("pointerdown", event => {
    const panel = event.target.closest(".dashboard-panel");
    if (!panel) return;
    focusDashboard(panel);
    if (boardDrag || !event.isPrimary || event.button !== 0 || document.fullscreenElement === panel) return;
    if (graphViewport.hasAttribute("data-layout-locked")) return;
    const resizeHandle = panel.querySelector('[data-dash="resize"]');
    const handle = panel.querySelector(".dock-heading");
    const resize = event.target === resizeHandle;
    if (!resize && (!handle.contains(event.target) || event.target.closest("button"))) return;
    cancelWheelZoom();
    boardDrag = {
      panel, id: event.pointerId, resize, x: event.clientX, y: event.clientY,
      scrollLeft: graphViewport.scrollLeft, scrollTop: graphViewport.scrollTop, bounds: dashboardBounds(panel),
      before: isVisualizeMode() ? visibleVisualizeBounds() : null,
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

  function finishDashboardDrag(commit) {
    const { panel, id, before } = boardDrag;
    boardDrag = null;
    if (panel.hasPointerCapture(id)) panel.releasePointerCapture(id);
    panel.classList.remove("is-resizing", "is-moving");
    delete panel.dataset.resizeSize;
    graphViewport.classList.remove("is-dragging-dashboard");
    if (isVisualizeMode() && before) {
      if (commit) commitVisualizeBounds(before);
      renderVisualizeLayout();
    } else if (commit && !isVisualizeMode() && !before) {
      saveCanvasBounds(panel);
      layoutGraph();
    }
  }

  graph.addEventListener("pointercancel", event => {
    if (boardDrag?.id === event.pointerId && boardDrag.before) finishDashboardDrag(false);
  });

  graph.addEventListener("lostpointercapture", event => {
    if (boardDrag?.id === event.pointerId) finishDashboardDrag(true);
  });

  graph.addEventListener("keydown", event => {
    const panel = event.target.closest(".dashboard-panel");
    if (!panel) return;
    const handle = panel.querySelector(".dock-heading");
    if (boardDrag || event.target !== handle || document.fullscreenElement === panel) return;
    if (graphViewport.hasAttribute("data-layout-locked")) return;
    const direction = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] }[event.key];
    if (!direction) return;
    event.preventDefault();
    cancelWheelZoom();
    const before = isVisualizeMode() ? visibleVisualizeBounds() : null;
    changeDashboardBounds(panel, dashboardBounds(panel), direction[0] * WORKSPACE_STEP, direction[1] * WORKSPACE_STEP, event.shiftKey);
    if (before) {
      commitVisualizeBounds(before);
      renderVisualizeLayout();
    } else {
      saveCanvasBounds(panel);
      layoutGraph();
    }
  });

  return { visibleDashboards, revealDashboard, releaseDashboard, setVisualizeModeLayout, updateVisualizeScrollHeight };
}
