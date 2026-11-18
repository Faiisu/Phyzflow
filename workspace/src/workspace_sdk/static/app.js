import { createEdgeRouter, pointAlongEdge, traceEdge } from "./edge-routing.js";
import { createCanvasLayout, snapStep } from "./canvas-layout.js";
import { initDashboards } from "./dashboard-ui.js";

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
const form = document.querySelector("#run-form");
const input = document.querySelector("#input");
const button = document.querySelector("#run-flow");
const runInput = document.querySelector("#run-input");
const resetInput = document.querySelector("#reset-input");
const status = document.querySelector("#status");
const result = document.querySelector("#result");
const outline = document.querySelector("#outline-list");
const panelSwitcher = document.querySelector("#panel-switcher");
const panelToggles = [...panelSwitcher.querySelectorAll("button[aria-controls]")];
const inspectorState = document.querySelector("#inspector-state");
const inspectorDuration = document.querySelector("#inspector-duration");
const copySource = document.querySelector("#copy-source");
const openNodeFiles = document.querySelector("#open-node-files");
const nodeFilesDialog = document.querySelector("#node-files");
const nodeFilesHeading = document.querySelector("#node-files-heading");
const nodeFilesPath = document.querySelector("#node-files-path");
const nodeFilesTree = document.querySelector("#node-files-tree");
const nodeFilesMessage = document.querySelector("#node-files-message");
const nodeSourceForm = document.querySelector("#node-source-form");
const nodeSourceInput = document.querySelector("#node-source-input");
const nodeCodeEditor = document.querySelector("#node-code-editor");
const nodeSourceHighlight = document.querySelector("#node-source-highlight");
const nodeSourceName = document.querySelector("#node-source-name");
const nodeSourceMessage = document.querySelector("#node-source-message");
const saveNodeSource = document.querySelector("#save-node-source");
const historyList = document.querySelector("#history-list");
const historyDetail = document.querySelector("#history-detail");
const refreshHistoryButton = document.querySelector("#refresh-history");
const nodes = new Map();
const labels = { running: "Running", succeeded: "Succeeded", failed: "Failed", skipped: "Skipped", stopped: "Stopped" };
let tracksGenerators = false;
let activeNode = null;
let stopPhase = null;
let runInProgress = false;
let streamingOwnRun = false;
let projectReady = false;
let dashboards;
let deferredRunError = null;
let liveOutput = null;
let graphZoom = 100;
let zoomSettings = null;
let wheelZoom = null;
const minimumZoom = 50;
const maximumZoom = 200;
const zoomStep = 25;
let zoomFloor = minimumZoom;
let edgePoints = [];

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
  const toolbar = rect("#panel-toolbar");
  const outlinePanel = rect("#panel-outline");
  const sidebar = rect(".overlay-stack");
  const controls = rect("#panel-zoom");
  const panelMenu = rect("#panel-switcher > summary");
  const width = viewport.width;
  const top = toolbar.height ? toolbar.bottom - viewport.top : 0;
  const bottom = Math.min(controls.height ? controls.top : viewport.bottom, panelMenu.top) - viewport.top;
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

fitGraph.addEventListener("click", () => {
  const items = [...Array.from(nodes.values(), item => item.node), ...edgeLabels.children, ...visibleDashboards()];
  const bounds = graphBounds(items);
  for (const point of edgePoints) {
    bounds.left = Math.min(bounds.left, point.x);
    bounds.top = Math.min(bounds.top, point.y);
    bounds.right = Math.max(bounds.right, point.x);
    bounds.bottom = Math.max(bounds.bottom, point.y);
  }
  frameGraphBounds(bounds, true);
});
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

function setPanel(name, visible) {
  const id = `panel-${name}`;
  const panel = document.getElementById(id);
  panel.hidden = !visible;
  panelToggles.find(toggle => toggle.getAttribute("aria-controls") === id)
    .setAttribute("aria-pressed", String(visible));
  return panel;
}

for (const toggle of panelToggles) {
  toggle.addEventListener("click", () => {
    const visible = toggle.getAttribute("aria-pressed") === "false";
    const panel = setPanel(toggle.getAttribute("aria-controls").slice(6), visible);
    if (visible) panel.scrollIntoView({ block: "nearest" });
  });
}

document.addEventListener("pointerdown", event => {
  if (panelSwitcher.open && !panelSwitcher.contains(event.target)) panelSwitcher.open = false;
});

panelSwitcher.addEventListener("keydown", event => {
  if (event.key !== "Escape" || !panelSwitcher.open) return;
  panelSwitcher.open = false;
  panelSwitcher.querySelector("summary").focus();
});

for (const button of document.querySelectorAll("[data-close-panel]")) {
  button.addEventListener("click", () => {
    setPanel(button.dataset.closePanel, false);
    panelSwitcher.querySelector("summary").focus();
  });
}

if (window.matchMedia("(max-width: 720px)").matches) setPanel("outline", false);
if (narrowGraph.matches) setPanel("inspector", false);

function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function sourceLanguage(path) {
  if (path.endsWith(".py")) return "python";
  if (path.endsWith(".json")) return "json";
  return null;
}

function appendSyntaxTokens(parent, tokens) {
  for (const token of tokens) {
    if (typeof token === "string") {
      parent.append(token);
    } else {
      const span = element("span", "", "token");
      span.classList.add(token.type, ...[token.alias ?? []].flat());
      appendSyntaxTokens(span, [token.content].flat());
      parent.append(span);
    }
  }
}

function highlightCode(code, text, language) {
  const prism = window.Prism;
  const grammar = prism?.languages[language];
  if (!grammar) {
    code.textContent = text;
    return false;
  }
  const preview = document.createDocumentFragment();
  appendSyntaxTokens(preview, prism.tokenize(text, grammar));
  code.replaceChildren(preview);
  return true;
}

function jsonOutput(text) {
  const code = element("code", "");
  highlightCode(code, text, "json");
  const pre = element("pre", "", "json-output");
  pre.append(code);
  return pre;
}

function syncSourceScroll() {
  nodeSourceHighlight.style.width = `${nodeSourceInput.clientWidth + 2}px`;
  nodeSourceHighlight.style.height = `${nodeSourceInput.clientHeight + 2}px`;
  nodeSourceHighlight.scrollTop = nodeSourceInput.scrollTop;
  nodeSourceHighlight.scrollLeft = nodeSourceInput.scrollLeft;
}

let highlightFrame;
function highlightSource() {
  if (!currentSource) return;
  const text = nodeSourceInput.value;
  const preview = text.endsWith("\n") ? `${text}\n` : text;
  const highlighted = highlightCode(nodeSourceHighlight.firstElementChild, preview, sourceLanguage(currentSource.path));
  nodeCodeEditor.toggleAttribute("data-highlighted", highlighted);
  syncSourceScroll();
}

nodeSourceInput.addEventListener("scroll", syncSourceScroll);
new ResizeObserver(syncSourceScroll).observe(nodeSourceInput);

let nodeFilesRequest;
let nodeSourceRequest;
let currentSource = null;
let savingSource = false;

function sourceIsDirty() {
  return currentSource !== null && nodeSourceInput.value !== currentSource.text;
}

function updateSourceDirty() {
  const dirty = sourceIsDirty();
  document.documentElement.toggleAttribute("data-unsaved-source", dirty || savingSource);
  saveNodeSource.disabled = (!dirty && currentSource?.revision !== "missing") ||
    savingSource || Boolean(nodeSourceRequest) || stopPhase === "stopped";
}

function canLeaveSource() {
  return !savingSource && (!sourceIsDirty() || window.confirm("Discard unsaved changes to this file?"));
}

window.addEventListener("beforeunload", event => {
  if (sourceIsDirty()) {
    event.preventDefault();
    event.returnValue = true;
  }
});
document.querySelector("#close-node-files").addEventListener("click", () => {
  if (canLeaveSource()) nodeFilesDialog.close();
});
nodeFilesDialog.addEventListener("cancel", event => {
  if (!canLeaveSource()) event.preventDefault();
});
nodeFilesDialog.addEventListener("close", () => {
  if (!nodeFilesDialog.open) {
    nodeFilesRequest?.abort();
    nodeSourceRequest?.abort();
    nodeSourceRequest = null;
    currentSource = null;
    updateSourceDirty();
  }
});

async function requestSource(index, data, signal) {
  const response = await fetch(`node-source/${index}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
    signal,
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const fallback = response.status === 409
      ? "The flow was reloaded. Copy any unsaved changes before reopening this file."
      : "Could not access the source file.";
    throw new Error(result.error ?? fallback);
  }
  return result;
}

async function openSource(index, path, line = null) {
  if (stopPhase === "stopped" || !canLeaveSource()) return;
  nodeSourceRequest?.abort();
  const request = new AbortController();
  nodeSourceRequest = request;
  nodeSourceInput.readOnly = true;
  saveNodeSource.disabled = true;
  nodeSourceMessage.textContent = "Loading source…";
  try {
    const { source, revision } = await requestSource(index, { path }, request.signal);
    if (request.signal.aborted) return;
    currentSource = { index, path, text: source, revision };
    nodeSourceName.textContent = path;
    nodeSourceInput.value = source;
    nodeSourceForm.hidden = false;
    nodeSourceInput.scrollTop = nodeSourceInput.scrollLeft = 0;
    highlightSource();
    nodeSourceMessage.textContent = revision === "missing" ? "Save to create settings.json." : "";
    if (Number.isInteger(line) && line > 0) {
      const lines = nodeSourceInput.value.split("\n", line);
      if (lines.length === line) {
        const end = lines.join("\n").length;
        const lineHeight = parseFloat(getComputedStyle(nodeSourceInput).lineHeight);
        nodeSourceInput.focus();
        nodeSourceInput.setSelectionRange(end - lines.at(-1).length, end);
        nodeSourceInput.scrollTop = Math.max(0, (line - 1) * lineHeight - nodeSourceInput.clientHeight / 3);
        syncSourceScroll();
        nodeSourceMessage.textContent = `Line ${line} selected.`;
      } else {
        nodeSourceMessage.textContent = `Line ${line} is unavailable in the current file.`;
      }
    }
    for (const button of nodeFilesTree.querySelectorAll("[data-file]")) {
      button.setAttribute("aria-current", String(button.dataset.file === path));
    }
  } catch (error) {
    if (!request.signal.aborted) nodeSourceMessage.textContent = error.message;
  } finally {
    if (nodeSourceRequest === request) {
      nodeSourceRequest = null;
      nodeSourceInput.readOnly = stopPhase === "stopped";
      updateSourceDirty();
    }
  }
}

nodeSourceInput.addEventListener("input", () => {
  updateSourceDirty();
  cancelAnimationFrame(highlightFrame);
  highlightFrame = requestAnimationFrame(highlightSource);
  nodeSourceMessage.textContent = "";
});
nodeSourceForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (stopPhase === "stopped" || savingSource || nodeSourceRequest || !currentSource ||
      (!sourceIsDirty() && currentSource.revision !== "missing")) return;
  const { index, path, revision } = currentSource;
  const source = nodeSourceInput.value;
  savingSource = true;
  updateSourceDirty();
  nodeSourceInput.readOnly = true;
  nodeSourceMessage.textContent = "Saving…";
  try {
    const saved = await requestSource(index, { path, source, revision });
    currentSource = { index, path, text: source, revision: saved.revision };
    nodeSourceMessage.textContent = window.parent === window
      ? "Saved. Restart the flow UI to run the edited code."
      : "Saved. Close the editor and select Reload flow to run the edited code.";
  } catch (error) {
    nodeSourceMessage.textContent = error.message;
  } finally {
    savingSource = false;
    nodeSourceInput.readOnly = stopPhase === "stopped";
    updateSourceDirty();
  }
});

function fileTree(entries, parent, source, index) {
  const list = element("ul", "", "file-tree");
  for (const entry of entries) {
    const path = parent ? `${parent}/${entry.name}` : entry.name;
    const item = element("li", "");
    if (entry.children === null) {
      const editable = sourceLanguage(path) !== null;
      const file = element(editable ? "button" : "code", entry.name, path === source ? "node-entrypoint" : "node-file");
      if (editable) {
        file.type = "button";
        file.dataset.file = path;
        file.addEventListener("click", () => openSource(index, path));
      }
      item.append(file);
    } else {
      const folder = element("details", "");
      folder.append(element("summary", entry.name), fileTree(entry.children, path, source, index));
      if (source?.startsWith(`${path}/`)) folder.open = true;
      item.append(folder);
    }
    list.append(item);
  }
  return list;
}

async function showNodeFiles(id, index, source, line = null, preferSettings = false) {
  if (stopPhase === "stopped" || !canLeaveSource()) return;
  nodeFilesRequest?.abort();
  nodeSourceRequest?.abort();
  nodeSourceRequest = null;
  currentSource = null;
  updateSourceDirty();
  nodeSourceForm.hidden = true;
  nodeSourceMessage.textContent = "Select a Python or JSON file.";
  const request = new AbortController();
  nodeFilesRequest = request;
  nodeFilesHeading.textContent = `${id} files`;
  nodeFilesPath.hidden = true;
  nodeFilesTree.replaceChildren();
  nodeFilesMessage.textContent = "Loading files…";
  if (!nodeFilesDialog.open) nodeFilesDialog.showModal();
  try {
    const response = await fetch(`node-files/${index}`, { signal: request.signal });
    if (!response.ok) throw new Error();
    const { path, entries, truncated, settings } = await response.json();
    if (request.signal.aborted) return;
    if (path === null) {
      nodeFilesMessage.textContent = "No source file was found inside this flow.";
      return;
    }
    nodeFilesPath.textContent = path === "." ? "Flow directory" : path;
    nodeFilesPath.hidden = false;
    nodeFilesTree.replaceChildren(fileTree(entries, path === "." ? "" : path, source, index));
    nodeFilesMessage.textContent = truncated ? "Showing part of this folder tree." : "";
    const listed = [...nodeFilesTree.querySelectorAll("[data-file]")].some(button => button.dataset.file === source);
    if (preferSettings && settings) await openSource(index, settings);
    else if (listed || line !== null) await openSource(index, source, line);
  } catch (error) {
    if (!request.signal.aborted) nodeFilesMessage.textContent = "Could not load the node files. Close this view and try again.";
  }
}

function formatDuration(value) {
  if (!Number.isFinite(value)) return "";
  if (value > 0 && value < 0.001) return "<0.001 ms";
  return `${value.toFixed(value < 1 ? 3 : 2)} ms`;
}

const inspectorTabs = [...document.querySelectorAll(".inspector-tabs [role=tab]")];
const executionRunLabel = document.querySelector("#execution-run-label");
const currentRunButton = document.querySelector("#execution-current-run");
const executionNote = document.querySelector("#execution-note");
const executionTiming = document.querySelector("#execution-timing");
const executionStatus = document.querySelector("#execution-status");
const executionStarted = document.querySelector("#execution-started");
const executionFinished = document.querySelector("#execution-finished");
const executionDuration = document.querySelector("#execution-duration");
const executionMetrics = document.querySelector("#execution-metrics");
const executionMetricGroups = document.querySelector("#execution-metric-groups");
const currentNodeTiming = new Map();
const completedRunStates = ["succeeded", "failed"];
let currentRunId = null;
let currentRunFinished = false;
let executionHistory = null;

function selectInspectorTab(selected) {
  for (const tab of inspectorTabs) {
    const active = tab === selected;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    document.getElementById(tab.getAttribute("aria-controls")).hidden = !active;
  }
}

for (const [index, tab] of inspectorTabs.entries()) {
  tab.addEventListener("click", () => selectInspectorTab(tab));
  tab.addEventListener("keydown", event => {
    const target = {
      ArrowLeft: inspectorTabs.at(index - 1),
      ArrowRight: inspectorTabs[(index + 1) % inspectorTabs.length],
      Home: inspectorTabs[0],
      End: inspectorTabs.at(-1),
    }[event.key];
    if (!target) return;
    event.preventDefault();
    selectInspectorTab(target);
    target.focus();
  });
}

function showExecutionTime(container, timestamp, fallback) {
  if (typeof timestamp !== "string" || !Number.isFinite(Date.parse(timestamp))) {
    container.textContent = fallback;
    return;
  }
  const time = element("time", timestamp.replace("T", " ").replace(/(?:\+00:00|Z)$/, " UTC"));
  time.dateTime = timestamp;
  container.replaceChildren(time);
}

function renderInspectorExecution() {
  const timing = executionHistory
    ? executionHistory.steps?.find(step => step.id === activeNode)
    : currentNodeTiming.get(activeNode);
  const runId = executionHistory?.id ?? currentRunId;
  executionRunLabel.textContent = runId ? `${executionHistory ? "Saved run" : "Current run"} · ${runId}` : "";
  currentRunButton.hidden = !executionHistory;
  executionTiming.hidden = !timing;
  executionNote.hidden = Boolean(timing);
  const completed = executionHistory
    ? completedRunStates.includes(executionHistory.status)
    : currentRunFinished;
  renderExecutionMetrics(completed ? timing?.metrics : null);

  if (!timing) {
    executionNote.textContent = executionHistory?.message ?? (executionHistory
      ? "This node has no execution record in the selected run."
      : currentRunId ? "No execution event received for this node." : "Run the flow or select a saved run.");
    return;
  }

  executionStatus.textContent = labels[timing.status] ?? "Unavailable";
  const skipped = timing.status === "skipped";
  const running = timing.status === "running";
  showExecutionTime(executionStarted, skipped ? null : timing.started_at, skipped ? "Not run" : "Unavailable");
  // Older saved runs recorded the last event time but omitted the start timestamp.
  const finished = skipped || running ? null : timing.finished_at ?? timing.time;
  showExecutionTime(executionFinished, finished, skipped ? "Not run" : running ? "Running" : "Unavailable");
  executionDuration.textContent = skipped ? "Not measured" : formatDuration(timing.duration_ms) || (running ? "Running" : "Unavailable");
}

function renderExecutionMetrics(metrics) {
  const groups = Object.entries(metrics ?? {}).map(([name, values], index) => {
    const group = element("div", "", "execution-metric-group");
    const title = element("h4", name);
    title.id = `execution-metric-group-${index}`;
    group.setAttribute("role", "group");
    group.setAttribute("aria-labelledby", title.id);
    const list = element("dl");
    for (const [label, value] of Object.entries(values)) {
      const row = element("div");
      row.append(element("dt", label), element("dd", String(value)));
      list.append(row);
    }
    group.append(title, list);
    return group;
  });
  executionMetrics.hidden = groups.length === 0;
  executionMetricGroups.replaceChildren(...groups);
}

currentRunButton.addEventListener("click", () => {
  executionHistory = null;
  renderInspectorExecution();
});

function trackInspectorExecution(message) {
  if (!message.run_id) return;
  if (!message.step_id) {
    if (message.status === "running") {
      currentRunId = message.run_id;
    }
    currentRunFinished = completedRunStates.includes(message.status);
    renderInspectorExecution();
    return;
  }

  const timing = currentNodeTiming.get(message.step_id) ?? {};
  timing.status = message.status;
  if (message.status === "running") {
    timing.started_at = message.time;
  } else if (message.status !== "skipped") {
    timing.finished_at = message.time;
    timing.duration_ms = message.duration_ms;
    timing.metrics = message.metrics;
  }
  currentNodeTiming.set(message.step_id, timing);
}

function showInspectorState(value, duration = "") {
  inspectorState.textContent = labels[value] ?? "Idle";
  inspectorState.dataset.state = value ?? "idle";
  inspectorDuration.textContent = duration;
  inspectorDuration.hidden = !duration;
  const skipped = document.querySelector("#inspector-blocked-by");
  const blockers = nodes.get(activeNode)?.blockedBy ?? [];
  skipped.textContent = blockers.length ? blockedByText(blockers, id => nodes.get(id)?.node.dataset.state) : "";
  skipped.hidden = !blockers.length;
  renderInspectorExecution();
}

function blockedByText(blockers, statusFor) {
  const names = blockers.map(id => {
    const status = labels[statusFor(id)];
    return status ? `${id} (${status})` : id;
  });
  return `Blocked by ${names.join(", ")}`;
}

function focusGraphNode(id) {
  const { node, select } = nodes.get(id);
  select.click();
  frameGraphBounds(graphBounds([node]));
  select.focus({ preventScroll: true });
}

function dependencyButton(id) {
  const button = element("button", id, "inspector-dependency");
  button.type = "button";
  button.addEventListener("click", () => focusGraphNode(id));
  return button;
}

function inputBindings(step, source) {
  return step.arguments
    .filter(argument => argument.kind === "node" && argument.source === source)
    .map(argument => argument.name);
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
  const { node, select, outlineSelect, index, duration } = nodes.get(step.id);
  openNodeFiles.onclick = () => showNodeFiles(step.id, index, step.source);
  document.querySelector("#visualize-node").onclick = () => dashboards.open(step.id);
  node.classList.add("is-selected");
  select.setAttribute("aria-pressed", "true");
  outlineSelect.setAttribute("aria-pressed", "true");
  document.querySelector("#inspector-empty").hidden = true;
  document.querySelector("#inspector-detail").hidden = false;
  document.querySelector("#inspector-name").textContent = step.id;
  showInspectorState(node.dataset.state, duration.textContent);

  const description = document.querySelector("#inspector-description");
  description.textContent = step.description;
  description.hidden = !step.description;
  const source = document.querySelector("#inspector-source");
  source.textContent = locationText({ file: step.source, line: step.line });
  source.title = locationText(step);
  copySource.hidden = !step.file;
  copySource.textContent = "Copy location";
  copySource.onclick = async () => {
    let feedback = "Location copied";
    try {
      await navigator.clipboard.writeText(locationText(step));
    } catch (error) {
      feedback = "Copy failed";
    }
    if (activeNode === step.id) copySource.textContent = feedback;
  };

  const inputs = step.arguments.map(argument => {
    const item = element("li", argument.name);
    item.append(argument.kind === "node"
      ? dependencyButton(argument.source)
      : element("small", `Run input · ${argument.source}`));
    return item;
  });
  const feeds = project.steps.filter(next => next.dependencies.includes(step.id)).map(next => {
    const item = element("li", "");
    item.append(dependencyButton(next.id), element("small", inputBindings(next, step.id).join(", ")));
    return item;
  });
  if (step.id === project.output) feeds.push(element("li", "Run output"));
  document.querySelector("#inspector-inputs").replaceChildren(...(inputs.length ? inputs : [element("li", "None")]));
  document.querySelector("#inspector-feeds").replaceChildren(...(feeds.length ? feeds : [element("li", "None")]));
  drawEdges(project.steps);
}

function positionGraph(project) {
  const { steps } = project;
  tracksGenerators = steps.some(step => step.track_generator);
  const rows = new Map();
  const producers = new Set(steps.flatMap(step => step.dependencies));
  for (const [index, step] of steps.entries()) {
    const column = Math.max(0, ...step.dependencies.map(id => nodes.get(id).column)) + 1;
    const row = (rows.get(column) ?? 0) + 1;
    rows.set(column, row);

    const node = element("article", "", "node");
    node.style.setProperty("--level", column);
    node.style.setProperty("--lane", row);
    node.classList.toggle("has-input", step.dependencies.length > 0);
    node.classList.toggle("has-output", producers.has(step.id));
    const state = element("p", "Idle", "state badge");
    const duration = element("span", "", "node-duration");
    duration.hidden = true;
    const metrics = element("div", "", "node-metrics");
    metrics.append(state, duration);
    const number = String(index + 1).padStart(2, "0");
    const select = element("button", "", "inspect-node");
    select.type = "button";
    select.setAttribute("aria-label", `Inspect ${step.id}`);
    select.setAttribute("aria-pressed", "false");
    select.addEventListener("click", () => inspectNode(step, project));
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
    graph.append(node);
    nodes.set(step.id, { node, column, state, duration, select, outlineSelect, index });
  }
  const count = steps.reduce((total, step) => total + step.dependencies.length, 0);
  document.querySelector("#graph-summary").textContent =
    `${steps.length} ${steps.length === 1 ? "node" : "nodes"} / ${count} ${count === 1 ? "edge" : "edges"}`;
  layoutGraph(graphZoom, true);
  if (steps.length) inspectNode(steps[0], project);
  enableNodeDragging(steps);
  const graphObserver = new ResizeObserver(() => {
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

function drawEdges(steps) {
  const { left, top, right, bottom } = graphDrawingBounds(graph.offsetWidth, graph.offsetHeight);
  const width = right - left;
  const height = bottom - top;
  const scale = window.devicePixelRatio || 1;
  canvas.style.left = `${left}px`;
  canvas.style.top = `${top}px`;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  canvas.width = Math.ceil(width * scale);
  canvas.height = Math.ceil(height * scale);
  const ctx = canvas.getContext("2d");
  ctx.scale(scale, scale);
  ctx.translate(-left, -top);
  const vertical = narrowGraph.matches;
  if (vertical) ctx.transform(0, 1, 1, 0, 0, 0);
  ctx.lineJoin = "round";
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
  const edges = steps.flatMap((step, target) => step.dependencies.map(source => ({
    step, source, target, direction: activeNode === step.id ? "in" : activeNode === source ? "out" : null,
  })));
  const tags = [];
  const labelBoxes = [];
  edgePoints = [];
  for (const edge of [...edges.filter(edge => !edge.direction), ...edges.filter(edge => edge.direction)]) {
    const path = route(nodes.get(edge.source).index, edge.target);
    for (const point of path.points) edgePoints.push(vertical ? { x: point.y, y: point.x } : point);
    const end = path.points.at(-1);
    const color = edge.direction ? edgeColors[edge.direction] : "#73788770";
    ctx.lineWidth = edge.direction ? 2.2 : 1.4;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.beginPath();
    traceEdge(ctx, path);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(end.x, end.y);
    ctx.lineTo(end.x - 6, end.y - 4);
    ctx.lineTo(end.x - 6, end.y + 4);
    ctx.fill();
    if (!edge.direction) continue;
    const bindings = inputBindings(edge.step, edge.source).join(", ");
    const tag = element("span", bindings, "edge-label");
    tag.dataset.direction = edge.direction;
    const halfWidth = (bindings.length * 6 + 8) / 2;
    const above = !vertical && Boolean(path.cubic);
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
}

function setStatus(value, label = labels[value]) {
  status.textContent = label;
  status.dataset.state = value;
}

function showMessage(message) {
  if (stopPhase === "stopped") return;
  if (message.live_frame || message.live_metrics || message.setting_change) return;
  if (message.output_preview !== undefined) {
    if (!liveOutput) {
      liveOutput = element("section");
      liveOutput.setAttribute("aria-label", "Live output");
      liveOutput.append(element("h3", "Live output"));
      result.append(liveOutput);
    }
    liveOutput.querySelector("pre")?.remove();
    liveOutput.append(jsonOutput(message.output_preview));
    return;
  }
  trackInspectorExecution(message);
  if (message.history_error) {
    result.append(element("p", message.history_error, "error"));
    return;
  }
  if (message.error) {
    setStatus("failed");
    result.append(element("p", message.error, "error"));
    return;
  }
  if (message.result) {
    setStatus(message.result.status);
    if (message.result.status === "succeeded") {
      result.append(element("h3", "Output"), jsonOutput(JSON.stringify(message.result.output, null, 2)));
    }
    return;
  }
  if (!message.step_id) {
    setStatus(message.status);
    return;
  }
  const entry = nodes.get(message.step_id);
  const { node, state, duration, index } = entry;
  entry.blockedBy = message.blocked_by;
  node.dataset.state = message.status;
  state.textContent = labels[message.status];
  duration.textContent = formatDuration(message.duration_ms);
  duration.hidden = !duration.textContent;
  if (activeNode === message.step_id) showInspectorState(message.status, duration.textContent);
  if (message.status === "failed") {
    const error = element("article", "", "error");
    const actions = element("div", "", "error-actions");
    const focus = element("button", "Focus node", "text-button");
    focus.type = "button";
    focus.addEventListener("click", () => focusGraphNode(message.step_id));
    actions.append(focus);
    if (message.source_target) {
      const source = element("button", "Open source", "text-button open-error-source");
      source.type = "button";
      source.addEventListener("click", () => showNodeFiles(message.step_id, index, message.source_target, message.line));
      actions.append(source);
    }
    const trace = document.createElement("details");
    trace.open = true;
    trace.append(element("summary", "Diagnostics"), element("pre",
      message.traceback ?? "See saved run history for diagnostic locations."));
    error.append(
      element("h3", `${message.step_id} failed`),
      element("strong", message.error_type),
      element("p", locationText(message)),
      actions,
      trace,
    );
    if (message.blocked_by?.length) {
      error.append(element("p", blockedByText(message.blocked_by, id => nodes.get(id)?.node.dataset.state)));
    }
    result.append(error);
  }
}

function locationText(location) {
  if (!location.file) return "Source unavailable";
  return location.line == null ? location.file : `${location.file}:${location.line}`;
}

function showMetadata(container, label, metadata) {
  if (metadata != null) {
    container.append(element("p", label), jsonOutput(JSON.stringify(metadata, null, 2)));
  }
}

function showSample(container, label, sample) {
  if (sample !== undefined) {
    container.append(element("p", label));
    if (sample === null) {
      container.append(element("p", "No JSON sample available."));
    } else {
      container.append(jsonOutput(sample));
    }
  }
}

let historySelection = 0;
let selectedRunId = null;
async function showHistoryRun(id) {
  if (stopPhase === "stopped") return;
  const selection = ++historySelection;
  selectedRunId = id;
  executionHistory = { id, message: "Loading saved run…" };
  renderInspectorExecution();
  for (const button of historyList.querySelectorAll(".history-run")) {
    button.setAttribute("aria-current", String(button.dataset.runId === id));
  }
  historyDetail.replaceChildren(element("p", "Loading run…"));
  try {
    const response = await fetch(`history/${id}`);
    if (!response.ok) throw new Error("Could not load this run.");
    const run = await response.json();
    if (selection !== historySelection) return;
    if (executionHistory?.id === id) {
      executionHistory = run;
      renderInspectorExecution();
    }
    historyDetail.replaceChildren(
      element("h3", "Saved run"),
      element("p", run.id, "run-id"),
      element("p", "Source version"),
      element("code", run.source_version, "source-version"),
      element("p", `Duration ${run.duration_ms.toFixed(2)} ms`),
    );
    showMetadata(historyDetail, "Run input", run.input);
    showMetadata(historyDetail, "Run output", run.output);
    const runSteps = new Map(run.steps.map(step => [step.id, step]));
    for (const step of run.steps) {
      const node = element("article", "", "history-node");
      node.append(
        element("h4", `${step.id} · ${labels[step.status]}`),
        element("p", locationText(step.source)),
      );
      const duration = formatDuration(step.duration_ms);
      if (duration) node.append(element("p", duration, "node-duration"));
      if (step.blocked_by?.length) {
        node.append(element("p", blockedByText(step.blocked_by, id => runSteps.get(id)?.status)));
      }
      showMetadata(node, "Input", step.input);
      showMetadata(node, "Output", step.output);
      if (step.reasons?.length) {
        const reasons = element("ul", "");
        for (const reason of step.reasons) reasons.append(element("li", reason));
        node.append(element("p", "Decision reasons"), reasons);
      }
      showSample(node, "Input sample", step.input_sample);
      showSample(node, "Output sample", step.output_sample);
      if (step.error_type) {
        const stack = element("ul", "", "stack-locations");
        for (const frame of step.error_frames) stack.append(element("li", locationText(frame)));
        node.append(element("strong", step.error_type), stack);
      }
      historyDetail.append(node);
    }
  } catch (error) {
    if (selection === historySelection) {
      if (executionHistory?.id === id) {
        executionHistory.message = error.message;
        renderInspectorExecution();
      }
      historyDetail.replaceChildren(element("p", error.message, "error"));
    }
  }
}

async function refreshHistory() {
  if (stopPhase === "stopped") return;
  refreshHistoryButton.disabled = true;
  try {
    const response = await fetch("history");
    if (!response.ok) throw new Error("Could not load run history.");
    const runs = await response.json();
    if (!runs.length) {
      historyList.replaceChildren(element("p", "No saved runs yet.", "muted"));
      return;
    }
    const list = element("ul", "");
    for (const run of runs) {
      const item = element("li", "");
      const select = element("button", "", "history-run");
      select.type = "button";
      select.dataset.runId = run.id;
      select.disabled = stopPhase === "stopped";
      select.setAttribute("aria-current", String(run.id === selectedRunId));
      const state = element("strong", labels[run.status], "badge");
      state.dataset.state = run.status;
      const date = element("time", new Date(run.started_at).toLocaleString());
      date.dateTime = run.started_at;
      select.append(state, date);
      select.addEventListener("click", () => showHistoryRun(run.id));
      item.append(select);
      list.append(item);
    }
    historyList.replaceChildren(list);
  } catch (error) {
    historyList.replaceChildren(element("p", error.message, "error"));
  } finally {
    refreshHistoryButton.disabled = stopPhase === "stopped";
  }
}

refreshHistoryButton.addEventListener("click", refreshHistory);
refreshHistory();
button.addEventListener("click", () => setPanel("run", true).scrollIntoView({ block: "nearest" }));

function updateRunControls() {
  button.disabled = runInput.disabled = !projectReady || runInProgress || Boolean(stopPhase);
}

function showRunStreamError(message) {
  dashboards.track({ error: message });
  setStatus("failed");
  result.append(element("p", message, "error run-stream-error"));
}

window.addEventListener("workspace-stop", event => {
  const { phase, discardUnsaved } = event.detail;
  stopPhase = phase;
  updateRunControls();
  if (phase === "stopped") {
    setStatus("stopped", "Stopped");
    if (tracksGenerators) {
      for (const [id, { node, state }] of nodes) {
        if (node.dataset.state !== "running") continue;
        node.dataset.state = "stopped";
        state.textContent = labels.stopped;
        const timing = currentNodeTiming.get(id);
        if (timing) timing.status = "stopped";
      }
      if (activeNode !== null && nodes.get(activeNode)?.node.dataset.state === "stopped") {
        showInspectorState("stopped");
      }
    }
    result.querySelector(".run-stream-error")?.remove();
    resetInput.disabled = openNodeFiles.disabled = refreshHistoryButton.disabled = true;
    for (const control of document.querySelectorAll(".history-run, .open-error-source")) control.disabled = true;
    nodeSourceInput.readOnly = true;
    saveNodeSource.disabled = true;
    if (discardUnsaved && nodeFilesDialog.open) nodeFilesDialog.close();
  } else if (!phase && deferredRunError) {
    showRunStreamError(deferredRunError);
  }
  if (phase !== "stopping") deferredRunError = null;
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (stopPhase || runInProgress || !projectReady) return;
  runInProgress = true;
  streamingOwnRun = true;
  updateRunControls();
  setPanel("run", true);
  result.replaceChildren();
  liveOutput = null;
  setStatus("running");
  currentRunId = null;
  currentRunFinished = false;
  currentNodeTiming.clear();
  for (const entry of nodes.values()) {
    const { node, state, duration } = entry;
    entry.blockedBy = undefined;
    delete node.dataset.state;
    state.textContent = "Idle";
    duration.textContent = "";
    duration.hidden = true;
  }
  showInspectorState();
  try {
    const payload = JSON.parse(input.value);
    if (payload === null || Array.isArray(payload) || typeof payload !== "object") {
      throw new Error("Enter a JSON object, then run the flow again.");
    }
    const response = await fetch("run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(`The flow could not start (${response.status}).`);
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    let receivedResult = false;
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += chunk.value;
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        const message = JSON.parse(line);
        dashboards.track(message);
        showMessage(message);
        if (message.result || message.error) receivedResult = true;
      }
    }
    if (!receivedResult) throw new Error("The run ended before returning a result.");
  } catch (error) {
    if (stopPhase === "stopping") deferredRunError = error.message;
    else if (!stopPhase) showRunStreamError(error.message);
  } finally {
    streamingOwnRun = false;
    runInProgress = false;
    updateRunControls();
  }
});

async function reconnectRun() {
  let cursor = 0;
  let observedRun = null;
  let wasRunning = false;
  while (!stopPhase) {
    try {
      if (streamingOwnRun) {
        await new Promise(resolve => setTimeout(resolve, 250));
        continue;
      }
      const response = await fetch("live-run");
      if (!response.ok) throw new Error("Could not reconnect to the running flow.");
      const snapshot = await response.json();
      // A POST stream already rendered this run; only attach to a different owner.
      const runId = snapshot.events.find(([, message]) => message.run_id)?.[1].run_id;
      if (runId && runId !== observedRun) {
        observedRun = runId;
        cursor = runId === currentRunId ? Infinity : 0;
      }
      runInProgress = snapshot.running;
      updateRunControls();
      for (const [sequence, message] of snapshot.events) {
        if (sequence <= cursor) continue;
        if (message.step_id && !nodes.has(message.step_id)) continue;
        dashboards.track(message);
        showMessage(message);
        cursor = sequence;
      }
      if (wasRunning && !snapshot.running) refreshHistory();
      if (snapshot.status === "stopped") setStatus("stopped");
      wasRunning = snapshot.running;
    } catch (error) {
      // A temporary disconnect must not mark the server-owned run as failed.
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
}

try {
  const response = await fetch("project");
  if (!response.ok) throw new Error("Could not load the project graph. Restart Workspace and try again.");
  const project = await response.json();
  dashboards = initDashboards(
    project,
    canvasLayout.revealDashboard,
    canvasLayout.releaseDashboard,
    canvasLayout.setVisualizeModeLayout,
  );
  zoomSettings = project.zoom;
  const name = document.querySelector("#project-name");
  name.textContent = project.name;
  name.title = project.name;
  name.hidden = false;
  positionGraph(project);
  const sample = JSON.stringify(project.input, null, 2);
  input.value = sample;
  resetInput.addEventListener("click", () => {
    input.value = sample;
    input.focus();
  });
  resetInput.disabled = stopPhase === "stopped";
  projectReady = true;
  updateRunControls();
  reconnectRun();
} catch (error) {
  setPanel("run", true);
  result.replaceChildren();
  showMessage({ error: error.message });
}
