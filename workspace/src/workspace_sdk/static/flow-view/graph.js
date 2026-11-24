import { createEdgeRouter, pointAlongEdge, tangentAlongEdge, traceEdge } from "../edge-routing.js";
import { createCanvasLayout, snapStep } from "../canvas-layout.js";
import { element, inputBindings } from "./display.js";

const maximumEdgeBitmapSide = 8192;
const maximumEdgeBitmapPixels = 16 * 1024 * 1024;

export function initGraph({ recoveryView, fitOnOpen, onSelect, revealInspector, showNodeFiles }) {
  const graph = document.querySelector("#graph");
  const canvas = graph.querySelector("canvas");
  const edgeLabels = document.createElement("div");
  edgeLabels.className = "edge-labels";
  edgeLabels.setAttribute("aria-hidden", "true");
  graph.append(edgeLabels);
  const graphPlane = document.querySelector("#graph-plane");
  const graphViewport = document.querySelector(".graph-scroll");
  let canvasLayout = null;
  function visibleDashboards() {
    return canvasLayout ? canvasLayout.visibleDashboards() : [];
  }
  const zoomIn = document.querySelector("#zoom-in");
  const zoomOut = document.querySelector("#zoom-out");
  const zoomReset = document.querySelector("#zoom-reset");
  const zoomLevel = document.querySelector("#zoom-level");
  const centerGraph = document.querySelector("#center-graph");
  const fitGraph = document.querySelector("#fit-graph");
  const fitSelection = document.querySelector("#fit-selection");
  const narrowGraph = window.matchMedia("(max-width: 600px)");
  const outline = document.querySelector("#outline-list");
  const nodes = new Map();
  let activeNode = null;
  let graphZoom = 100;
  let zoomSettings = null;
  let wheelZoom = null;
  const minimumZoom = 50;
  const maximumZoom = 200;
  const zoomStep = 25;
  let zoomFloor = minimumZoom;
  let edgePoints = [];
  let paintViewportEdges = null;
  let viewportEdgeFrame = null;

  function scheduleViewportEdgePaint() {
    if (!paintViewportEdges || viewportEdgeFrame !== null) return;
    viewportEdgeFrame = requestAnimationFrame(() => {
      viewportEdgeFrame = null;
      paintViewportEdges?.();
    });
  }
  graphViewport.addEventListener("scroll", scheduleViewportEdgePaint);

  function layoutSize(element) {
    const style = getComputedStyle(element);
    return { width: parseFloat(style.width), height: parseFloat(style.height) };
  }

  function graphAnchor(x = graphViewport.clientWidth / 2, y = graphViewport.clientHeight / 2) {
    return {
      x, y,
      graphX: (graphViewport.scrollLeft + x - (parseFloat(graph.style.left) || 0)) * 100 / graphZoom,
      graphY: (graphViewport.scrollTop + y - (parseFloat(graph.style.top) || 0)) * 100 / graphZoom,
    };
  }

  function clampZoom(zoom) {
    return Math.max(zoomFloor, Math.min(maximumZoom, zoom));
  }

  function graphDrawingBounds(width, height) {
    const bounds = { left: 0, top: 0, right: width, bottom: height };
    for (const { node } of nodes.values()) {
      if (!node.style.left) continue;
      bounds.left = Math.min(bounds.left, node.offsetLeft - 24);
      bounds.top = Math.min(bounds.top, node.offsetTop - 24);
      bounds.right = Math.max(bounds.right, node.offsetLeft + node.offsetWidth + 80);
      bounds.bottom = Math.max(bounds.bottom, node.offsetTop + node.offsetHeight + 80);
    }
    return bounds;
  }

  function layoutGraph(nextZoom = graphZoom, center = false, anchor = null) {
    if (document.body.classList.contains("is-visualize-mode")) {
      canvasLayout?.updateVisualizeScrollHeight();
      return;
    }
    // Use fractional outer dimensions so scrollbar visibility cannot resize the graph.
    const { width, height } = layoutSize(graphViewport);
    const { graphX, graphY } = anchor ?? graphAnchor();

    graphZoom = clampZoom(nextZoom);
    if (graphZoom >= minimumZoom) zoomFloor = minimumZoom;
    const scale = graphZoom / 100;
    graph.style.minWidth = `${width}px`;
    graph.style.minHeight = `${height}px`;
    const graphSize = layoutSize(graph);
    const bounds = graphDrawingBounds(graphSize.width, graphSize.height);
    for (const panel of visibleDashboards()) {
      bounds.left = Math.min(bounds.left, panel.offsetLeft - 24);
      bounds.top = Math.min(bounds.top, panel.offsetTop - 24);
      bounds.right = Math.max(bounds.right, panel.offsetLeft + panel.offsetWidth + 24);
      bounds.bottom = Math.max(bounds.bottom, panel.offsetTop + panel.offsetHeight + 24);
    }
    const scaledWidth = (bounds.right - bounds.left) * scale;
    const scaledHeight = (bounds.bottom - bounds.top) * scale;
    // Leave a viewport of room on each side so the graph can pan under the panels.
    const left = width + Math.max(0, (width - scaledWidth) / 2) - bounds.left * scale;
    const top = height + Math.max(0, (height - scaledHeight) / 2) - bounds.top * scale;
    graphPlane.style.width = `${2 * width + Math.max(width, scaledWidth)}px`;
    graphPlane.style.height = `${2 * height + Math.max(height, scaledHeight)}px`;
    graph.style.left = `${left}px`;
    graph.style.top = `${top}px`;
    graph.style.transform = `scale(${scale})`;
    graphViewport.scrollLeft = left + (center ? (bounds.left + bounds.right) * scale / 2 : graphX * scale) -
      (anchor?.x ?? graphViewport.clientWidth / 2);
    graphViewport.scrollTop = top + (center ? (bounds.top + bounds.bottom) * scale / 2 : graphY * scale) -
      (anchor?.y ?? graphViewport.clientHeight / 2);

    const label = graphZoom < 1 ? "<1%" : `${Math.round(graphZoom)}%`;
    if (zoomLevel.textContent !== label) zoomLevel.textContent = label;
    zoomIn.disabled = graphZoom >= maximumZoom;
    zoomOut.disabled = graphZoom <= zoomFloor;
    zoomReset.disabled = graphZoom === 100;
    centerGraph.disabled = false;
    fitGraph.disabled = nodes.size === 0;
    scheduleViewportEdgePaint();
  }

  function cancelWheelZoom() {
    if (!wheelZoom) return;
    cancelAnimationFrame(wheelZoom.frame);
    wheelZoom = null;
  }

  function setZoom(zoom, center = false) {
    cancelWheelZoom();
    layoutGraph(zoom, center);
  }

  zoomIn.addEventListener("click", () => setZoom(graphZoom + zoomStep));
  zoomOut.addEventListener("click", () => setZoom(graphZoom - zoomStep));
  zoomReset.addEventListener("click", () => setZoom(100));
  centerGraph.addEventListener("click", () => setZoom(graphZoom, true));

  function graphBounds(elements) {
    const graphRect = graph.getBoundingClientRect();
    const ratio = 100 / graphZoom;
    const rectangles = elements.map(element => element.getBoundingClientRect());
    return {
      left: (Math.min(...rectangles.map(rect => rect.left)) - graphRect.left) * ratio,
      top: (Math.min(...rectangles.map(rect => rect.top)) - graphRect.top) * ratio,
      right: (Math.max(...rectangles.map(rect => rect.right)) - graphRect.left) * ratio,
      bottom: (Math.max(...rectangles.map(rect => rect.bottom)) - graphRect.top) * ratio,
    };
  }

  function graphAreas() {
    const rect = selector => document.querySelector(selector).getBoundingClientRect();
    const viewport = graphViewport.getBoundingClientRect();
    const toolbar = document.querySelector("#panel-toolbar")?.getBoundingClientRect();
    const outlinePanel = rect("#panel-outline");
    const sidebar = rect(".overlay-stack");
    const controls = rect("#panel-zoom");
    const panelMenu = rect("#panel-switcher > summary");
    const width = viewport.width;
    const top = toolbar?.height ? toolbar.bottom - viewport.top : 0;
    const bottom = Math.min(
      controls.height ? controls.top : viewport.bottom,
      panelMenu.height ? panelMenu.top : viewport.bottom,
    ) - viewport.top;
    return [
      { left: 0, right: width, top: Math.max(top, outlinePanel.bottom - viewport.top, sidebar.bottom - viewport.top), bottom },
      { left: outlinePanel.width ? outlinePanel.right - viewport.left : 0,
        right: sidebar.height ? sidebar.left - viewport.left : width, top, bottom },
    ];
  }

  function frameGraphBounds(bounds, fit = false) {
    const areas = graphAreas();
    const { right: width, bottom } = areas[0];
    const top = areas[1].top;
    const margin = 16;
    const zoomFor = area => 100 * Math.min(
      (area.right - area.left - 2 * margin) / (bounds.right - bounds.left),
      (area.bottom - area.top - 2 * margin) / (bounds.bottom - bounds.top),
    );
    const available = areas.reduce((best, area) => zoomFor(area) > zoomFor(best) ? area : best);
    const area = zoomFor(available) > 0 ? available : { left: 0, right: width, top, bottom };
    let zoom = graphZoom;
    if (fit) {
      zoom = Math.max(0.001, zoomFor(area));
      // A large graph can need a wider overview than the manual zoom range.
      zoomFloor = Math.min(minimumZoom, zoom);
    }
    cancelWheelZoom();
    layoutGraph(zoom, false, {
      x: (area.left + area.right) / 2,
      y: (area.top + area.bottom) / 2,
      graphX: (bounds.left + bounds.right) / 2,
      graphY: (bounds.top + bounds.bottom) / 2,
    });
  }

  function fitGraphToView() {
    const items = [...Array.from(nodes.values(), item => item.node), ...edgeLabels.children, ...visibleDashboards()];
    const bounds = graphBounds(items);
    for (const point of edgePoints) {
      bounds.left = Math.min(bounds.left, point.x);
      bounds.top = Math.min(bounds.top, point.y);
      bounds.right = Math.max(bounds.right, point.x);
      bounds.bottom = Math.max(bounds.bottom, point.y);
    }
    frameGraphBounds(bounds, true);
  }

  fitGraph.addEventListener("click", fitGraphToView);
  fitSelection.addEventListener("click", () => frameGraphBounds(graphBounds([nodes.get(activeNode).node]), true));

  function animateWheelZoom(time) {
    const { start, from, target, anchor } = wheelZoom;
    const progress = Math.max(0, Math.min(1, (time - start) / zoomSettings.smooth_ms));
    const zoom = progress === 1 ? target : from + (target - from) * (1 - (1 - progress) ** 3);
    layoutGraph(zoom, false, anchor);
    if (progress === 1) wheelZoom = null;
    else wheelZoom.frame = requestAnimationFrame(animateWheelZoom);
  }

  graphViewport.addEventListener("wheel", event => {
    if (!zoomSettings || event.ctrlKey || event.metaKey || !event.deltaY ||
        event.target.closest(".dashboard-panel") || document.body.classList.contains("is-visualize-mode")) return;
    event.preventDefault();
    if (graphViewport.matches(".is-dragging-node, .is-dragging-dashboard")) return;
    const pixelUnit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? graphViewport.clientHeight : 1;
    const factor = Math.exp(-event.deltaY * pixelUnit * zoomSettings.speed * 0.002);
    const pendingTarget = wheelZoom?.target ?? graphZoom;
    const sameDirection = (pendingTarget - graphZoom) * (factor - 1) > 0;
    const target = clampZoom((sameDirection ? pendingTarget : graphZoom) * factor);
    const bounds = graphViewport.getBoundingClientRect();
    const anchor = graphAnchor(event.clientX - bounds.left, event.clientY - bounds.top);

    cancelWheelZoom();
    if (target === graphZoom) return;
    if (zoomSettings.smooth_ms === 0 || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      layoutGraph(target, false, anchor);
      return;
    }
    // Apply half the change in scale immediately, then ease to the target.
    const immediate = Math.sqrt(graphZoom * target);
    layoutGraph(immediate, false, anchor);
    wheelZoom = { start: performance.now(), from: graphZoom, target, anchor, frame: requestAnimationFrame(animateWheelZoom) };
  }, { passive: false });

  let pan = null;
  graphViewport.addEventListener("pointerdown", event => {
    if (pan || !event.isPrimary || event.button !== 0 || event.target.closest(".node, .dashboard-panel") ||
        graphViewport.classList.contains("is-dragging-node") || document.body.classList.contains("is-visualize-mode")) return;
    cancelWheelZoom();
    graphViewport.setPointerCapture(event.pointerId);
    graphViewport.classList.add("is-panning");
    pan = {
      id: event.pointerId,
      x: event.clientX + graphViewport.scrollLeft,
      y: event.clientY + graphViewport.scrollTop,
    };
    event.preventDefault();
  });

  graphViewport.addEventListener("pointermove", event => {
    if (pan?.id !== event.pointerId) return;
    graphViewport.scrollLeft = pan.x - event.clientX;
    graphViewport.scrollTop = pan.y - event.clientY;
  });

  graphViewport.addEventListener("lostpointercapture", event => {
    if (pan?.id !== event.pointerId) return;
    pan = null;
    graphViewport.classList.remove("is-panning");
  });

  canvasLayout = createCanvasLayout({
    graph,
    graphPlane,
    graphViewport,
    nodes,
    getZoom: () => graphZoom,
    graphAreas,
    graphAnchor,
    graphBounds,
    frameGraphBounds,
    layoutGraph,
    layoutSize,
    cancelWheelZoom,
  });

  function focusGraphNode(id) {
    const { node, select } = nodes.get(id);
    select.click();
    frameGraphBounds(graphBounds([node]));
    select.focus({ preventScroll: true });
  }

  function inspectNode(step, project) {
    if (activeNode !== null) {
      const previous = nodes.get(activeNode);
      previous.node.classList.remove("is-selected");
      previous.select.setAttribute("aria-pressed", "false");
      previous.outlineSelect.setAttribute("aria-pressed", "false");
    }
    activeNode = step.id;
    fitSelection.disabled = false;
    const { node, select, outlineSelect } = nodes.get(step.id);
    node.classList.add("is-selected");
    select.setAttribute("aria-pressed", "true");
    outlineSelect.setAttribute("aria-pressed", "true");
    onSelect(step, project);
    drawEdges(project.steps);
  }

  function positionGraph(project) {
    zoomSettings = project.zoom;
    const { steps } = project;
    const rows = new Map();
    const producers = new Set(steps.flatMap(step => step.dependencies));
    const loopProducers = new Set(steps.flatMap(step => step.loop_dependencies ?? []));
    for (const [index, step] of steps.entries()) {
      const column = Math.max(0, ...step.dependencies.map(id => nodes.get(id).column)) + 1;
      const row = (rows.get(column) ?? 0) + 1;
      rows.set(column, row);

      const node = element("article", "", "node");
      node.style.setProperty("--level", column);
      node.style.setProperty("--lane", row);
      const loopDependencies = step.loop_dependencies ?? [];
      node.classList.toggle("has-input", step.dependencies.length > 0 || loopDependencies.length > 0);
      node.classList.toggle("has-output", producers.has(step.id) || loopProducers.has(step.id));
      node.classList.toggle("has-loop", loopDependencies.length > 0 || loopProducers.has(step.id));
      const state = element("p", "Idle", "state badge");
      const iterations = element("span", "", "node-iterations");
      iterations.hidden = true;
      const duration = element("span", "", "node-duration");
      duration.hidden = true;
      const metrics = element("div", "", "node-metrics");
      metrics.append(state, iterations, duration);
      const number = String(index + 1).padStart(2, "0");
      const select = element("button", "", "inspect-node");
      select.type = "button";
      select.setAttribute("aria-label", `Inspect ${step.id}`);
      select.setAttribute("aria-pressed", "false");
      select.addEventListener("click", () => {
        inspectNode(step, project);
        revealInspector();
      });
      node.addEventListener("dblclick", () => showNodeFiles(step.id, index, step.source, null, true));
      const outlineSelect = element("button", "", "outline-node");
      outlineSelect.type = "button";
      outlineSelect.setAttribute("aria-pressed", "false");
      outlineSelect.append(element("span", number, "node-index"), element("span", step.id));
      outlineSelect.addEventListener("click", () => {
        inspectNode(step, project);
        node.scrollIntoView({ block: "nearest", inline: "nearest" });
      });
      const listItem = element("li", "");
      listItem.append(outlineSelect);
      outline.append(listItem);
      node.append(
        element("p", number, "node-index"),
        element("h3", step.id),
        metrics,
        select,
      );
      if (step.dependencies.length) {
        node.append(element("p", `Depends on ${step.dependencies.join(", ")}`, "sr-only"));
      }
      if (loopDependencies.length) {
        node.append(element("p", `Loops from ${loopDependencies.join(", ")}`, "sr-only"));
      }
      graph.append(node);
      nodes.set(step.id, { node, column, state, iterations, duration, select, outlineSelect, index });
    }
    const forwardCount = steps.reduce((total, step) => total + step.dependencies.length, 0);
    const loopCount = steps.reduce((total, step) => total + (step.loop_dependencies?.length ?? 0), 0);
    const count = forwardCount + loopCount;
    const loopSummary = loopCount ? ` (${loopCount} ${loopCount === 1 ? "loop" : "loops"})` : "";
    document.querySelector("#graph-summary").textContent =
      `${steps.length} ${steps.length === 1 ? "node" : "nodes"} / ${count} ${count === 1 ? "edge" : "edges"}${loopSummary}`;
    layoutGraph(graphZoom, true);
    if (steps.length) {
      inspectNode(steps[0], project);
      if (fitOnOpen) fitGraphToView();
    }
    if (!recoveryView) enableNodeDragging(steps);
    let pendingResize = false;
    function isSheetVisible() {
      if (document.hidden) return false;
      if (window.parent !== window && typeof window.parent.phyzflowSheetState === "function") {
        return Boolean(window.parent.phyzflowSheetState(window)?.active);
      }
      return true;
    }
    function flushPendingResize() {
      if (!pendingResize || !isSheetVisible()) return;
      pendingResize = false;
      layoutGraph();
      drawEdges(steps);
    }
    window.addEventListener("workspace-sheet-activity", flushPendingResize);
    document.addEventListener("visibilitychange", flushPendingResize);
    const graphObserver = new ResizeObserver(() => {
      if (!isSheetVisible()) {
        pendingResize = true;
        return;
      }
      pendingResize = false;
      layoutGraph();
      drawEdges(steps);
    });
    graphObserver.observe(graph);
    graphObserver.observe(graphViewport, { box: "border-box" });
  }

  function enableNodeDragging(steps) {
    let drag = null;
    let draggedButton = null;
    let redraw = null;

    graphViewport.addEventListener("pointerdown", event => {
      if (!event.isPrimary || event.button !== 0 || drag || pan) return;
      draggedButton = null;
      const button = event.target.closest(".inspect-node");
      if (!button) return;
      const node = button.closest(".node");
      cancelWheelZoom();
      button.setPointerCapture(event.pointerId);
      drag = {
        id: event.pointerId, button, node,
        x: event.clientX, y: event.clientY,
        left: parseFloat(node.style.left) || 0,
        top: parseFloat(node.style.top) || 0,
      };
    });

    graphViewport.addEventListener("pointermove", event => {
      if (drag?.id !== event.pointerId) return;
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      if (!draggedButton) {
        if (Math.hypot(dx, dy) < 4) return;
        draggedButton = drag.button;
        drag.button.click();
        drag.node.classList.add("is-dragging");
        graphViewport.classList.add("is-dragging-node");
      }
      const scale = graph.getBoundingClientRect().width / layoutSize(graph).width;
      drag.node.style.left = `${snapStep(drag.left + dx / scale)}px`;
      drag.node.style.top = `${snapStep(drag.top + dy / scale)}px`;
      if (redraw === null) {
        redraw = requestAnimationFrame(() => {
          redraw = null;
          drawEdges(steps);
        });
      }
    });

    graphViewport.addEventListener("lostpointercapture", event => {
      if (drag?.id !== event.pointerId) return;
      drag.node.classList.remove("is-dragging");
      graphViewport.classList.remove("is-dragging-node");
      drag = null;
      if (draggedButton) layoutGraph();
    });

    for (const type of ["click", "dblclick"]) {
      graphViewport.addEventListener(type, event => {
        if (event.detail && event.target === draggedButton) event.stopPropagation();
      }, true);
    }
  }

  function paintEdgeBitmap(strokes, vertical, left, top, width, height, pixelRatio, zoom) {
    canvas.style.left = `${left}px`;
    canvas.style.top = `${top}px`;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const bitmapWidth = Math.ceil(width * pixelRatio);
    const bitmapHeight = Math.ceil(height * pixelRatio);
    if (canvas.width !== bitmapWidth) canvas.width = bitmapWidth;
    if (canvas.height !== bitmapHeight) canvas.height = bitmapHeight;
    const ctx = canvas.getContext("2d");
    ctx.resetTransform();
    ctx.clearRect(0, 0, bitmapWidth, bitmapHeight);
    ctx.scale(pixelRatio, pixelRatio);
    ctx.translate(-left, -top);
    if (vertical) ctx.transform(0, 1, 1, 0, 0, 0);
    ctx.lineJoin = "round";
    for (const { line, arrows, color, lineWidth, dashed } of strokes) {
      ctx.lineWidth = Math.max(lineWidth, 1 / zoom);
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.setLineDash(dashed ? [6, 4] : []);
      ctx.stroke(line);
      ctx.setLineDash([]);
      for (const arrow of arrows) ctx.fill(arrow);
    }
  }

  function directionalArrow(tip, dir, size = 6, width = 4) {
    const arrow = new Path2D();
    const backX = tip.x - dir.x * size;
    const backY = tip.y - dir.y * size;
    const perpX = -dir.y * width;
    const perpY = dir.x * width;
    arrow.moveTo(tip.x, tip.y);
    arrow.lineTo(backX + perpX, backY + perpY);
    arrow.lineTo(backX - perpX, backY - perpY);
    return arrow;
  }

  function drawEdges(steps) {
    let { left, top, right, bottom } = graphDrawingBounds(graph.offsetWidth, graph.offsetHeight);
    const pixelRatio = window.devicePixelRatio || 1;
    const vertical = narrowGraph.matches;
    const screenCards = steps.map(step => {
      const { offsetLeft, offsetTop, offsetWidth, offsetHeight } = nodes.get(step.id).node;
      return { left: offsetLeft, right: offsetLeft + offsetWidth, top: offsetTop, bottom: offsetTop + offsetHeight };
    });
    const cards = vertical
      ? screenCards.map(card => ({ left: card.top, right: card.bottom, top: card.left, bottom: card.right }))
      : screenCards;
    const route = createEdgeRouter(cards);
    const style = getComputedStyle(graph);
    const edgeColors = {
      in: style.getPropertyValue("--edge-in").trim(),
      out: style.getPropertyValue("--edge-out").trim(),
    };
    const loopColor = style.getPropertyValue("--edge-loop").trim() || "#c792ea";
    const forwardEdges = steps.flatMap((step, target) => step.dependencies.map(source => ({
      step, source, target, loop: false,
      direction: activeNode === step.id ? "in" : activeNode === source ? "out" : null,
    })));
    let loopLane = 0;
    const loopEdges = steps.flatMap((step, target) => (step.loop_dependencies ?? []).map(source => ({
      step, source, target, loop: true, lane: loopLane++,
      direction: activeNode === step.id ? "in" : activeNode === source ? "out" : null,
    })));
    const edges = [...forwardEdges, ...loopEdges];
    const strokes = [];
    const tags = [];
    const labelBoxes = [];
    edgePoints = [];
    for (const edge of [...edges.filter(edge => !edge.direction), ...edges.filter(edge => edge.direction)]) {
      const path = route(nodes.get(edge.source).index, edge.target, { loop: edge.loop, lane: edge.lane ?? 0 });
      for (const point of path.points) {
        const screenPoint = vertical ? { x: point.y, y: point.x } : point;
        edgePoints.push(screenPoint);
        left = Math.min(left, screenPoint.x - 16);
        top = Math.min(top, screenPoint.y - 16);
        right = Math.max(right, screenPoint.x + 16);
        bottom = Math.max(bottom, screenPoint.y + 16);
      }
      const end = path.points.at(-1);
      const color = edge.direction ? edgeColors[edge.direction] : edge.loop ? `${loopColor}b3` : "#73788770";
      const line = new Path2D();
      traceEdge(line, path);
      const arrows = [directionalArrow(end, { x: 1, y: 0 })];
      if (edge.loop) {
        arrows.push(directionalArrow(pointAlongEdge(path, 0.5), tangentAlongEdge(path, 0.5), 6.5, 4.2));
      }
      strokes.push({ line, arrows, color, lineWidth: edge.direction ? 2.2 : edge.loop ? 1.8 : 1.4, dashed: edge.loop });
      if (!edge.direction) continue;
      const bindings = inputBindings(edge.step, edge.source, edge.loop ? "loop" : "node").join(", ");
      const tag = element("span", bindings, "edge-label");
      tag.dataset.direction = edge.direction;
      if (edge.loop) tag.dataset.loop = "true";
      const halfWidth = (bindings.length * 6 + (edge.loop ? 18 : 8)) / 2;
      const above = !vertical && (Boolean(path.cubic) || edge.loop);
      const candidates = [.5, .64, .36, .77, .23].map(fraction => {
        const point = pointAlongEdge(path, fraction);
        const x = vertical ? point.y : point.x;
        const y = vertical ? point.x : point.y;
        return { x, y, left: x - halfWidth, right: x + halfWidth,
          top: y - (above ? 18 : 7), bottom: y + (above ? -3 : 7) };
      });
      const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
      const position = candidates.find(box => screenCards.every(other => !overlaps(box, other)) &&
        labelBoxes.every(other => !overlaps(box, other))) ??
        candidates.find(box => labelBoxes.every(other => !overlaps(box, other))) ?? candidates[0];
      labelBoxes.push(position);
      tag.classList.toggle("edge-label-above", above);
      tag.style.left = `${position.x}px`;
      tag.style.top = `${position.y}px`;
      tags.push(tag);
    }
    edgeLabels.replaceChildren(...tags);

    const width = right - left;
    const height = bottom - top;
    const bitmapWidth = Math.ceil(width * pixelRatio);
    const bitmapHeight = Math.ceil(height * pixelRatio);
    const needsViewportBitmap = Math.max(bitmapWidth, bitmapHeight) > maximumEdgeBitmapSide ||
      bitmapWidth * bitmapHeight > maximumEdgeBitmapPixels;
    if (needsViewportBitmap) {
      paintViewportEdges = () => {
        const { clientWidth, clientHeight } = graphViewport;
        if (!clientWidth || !clientHeight) return;
        const zoom = graphZoom / 100;
        const { graphX, graphY } = graphAnchor(0, 0);
        const density = Math.min(pixelRatio, maximumEdgeBitmapSide / Math.max(clientWidth, clientHeight),
          Math.sqrt(maximumEdgeBitmapPixels / (clientWidth * clientHeight)));
        paintEdgeBitmap(strokes, vertical, graphX, graphY, clientWidth / zoom, clientHeight / zoom,
          density * zoom, zoom);
      };
      paintViewportEdges();
    } else {
      paintViewportEdges = null;
      paintEdgeBitmap(strokes, vertical, left, top, width, height, pixelRatio, 1);
    }
  }

  return {
    canvasLayout, nodes, get activeNode() { return activeNode; }, focusGraphNode, positionGraph,
  };
}
