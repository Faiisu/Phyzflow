import { createCanvasLayout } from "./canvas-layout.js";
import { fetchJSON, mountDashboardFrame } from "./dashboard-bridge.js";

document.title = "Phyzflow | Workspace Dashboard";
document.body.classList.add("is-visualize-mode", "is-workspace-dashboard");
document.querySelector("#graph-heading").textContent = "Workspace Dashboard";
const viewport = document.querySelector(".graph-scroll");
viewport.setAttribute("aria-label", "Workspace dashboard panels");

const menu = document.querySelector("#panel-switcher");
const drawer = document.querySelector("#panel-visualize");
document.querySelector("#visualize-heading").textContent = "Dashboard panels";
document.querySelector("#enter-visualize-mode").hidden = true;
const list = document.querySelector("#visualize-list");
const openAll = document.querySelector("#visualize-open-all");
const closeAll = document.querySelector("#visualize-close-all");
const refresh = document.querySelector("#visualize-refresh");
drawer.querySelector('[data-close-panel="visualize"]').addEventListener("click", () => {
  menu.open = false;
  menu.querySelector("summary").focus();
});
menu.querySelector(".panel-menu").replaceWith(drawer);
menu.addEventListener("keydown", event => {
  if (event.key !== "Escape" || !menu.open) return;
  menu.open = false;
  menu.querySelector("summary").focus();
  event.stopPropagation();
});

const back = document.querySelector("#exit-visualize-mode");
back.hidden = false;
back.textContent = "Back to sheets";

const status = document.createElement("p");
status.id = "workspace-dashboard-message";
status.className = "overlay";
status.setAttribute("role", "status");
document.querySelector(".stage").append(status);

if (document.fullscreenEnabled) {
  const fullscreen = document.createElement("button");
  fullscreen.type = "button";
  fullscreen.className = "text-button";
  fullscreen.textContent = "Full screen dashboard";
  document.addEventListener("fullscreenchange", () => {
    fullscreen.textContent = document.fullscreenElement === document.documentElement
      ? "Exit full screen dashboard" : "Full screen dashboard";
  });
  fullscreen.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement === document.documentElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      status.textContent = "Could not change dashboard full screen mode.";
      status.hidden = false;
    }
  });
  drawer.querySelector(".visualize-actions").append(fullscreen);
}

const graph = document.querySelector("#graph");
const layout = createCanvasLayout({
  graph,
  persistInitialBounds: true,
  graphPlane: document.querySelector("#graph-plane"),
  graphViewport: viewport,
  layoutSize(element) {
    const { width, height } = element.getBoundingClientRect();
    return { width, height };
  },
  cancelWheelZoom: () => {},
});
layout.setVisualizeModeLayout(true);

const template = document.querySelector("#dashboard");
template.remove();
const panels = new Map();
const closed = new Set();
let placements = [];
let catalog = [];
let busy = false;

function showStatus(message) {
  status.textContent = message;
  status.hidden = !message;
}

function panelTitle(source) {
  return [source.sheet_name, source.scope ?? "Workflow", source.label].join(" · ");
}

function sourceKey(source) {
  return JSON.stringify([source.sheet, source.scope, source.panel_id ?? source.id]);
}

function layoutKey(id) {
  return JSON.stringify([null, id]);
}

function closePanel(id) {
  const panel = panels.get(id);
  closed.add(id);
  if (!panel) return;
  panels.delete(id);
  panel.dispose();
  if (document.fullscreenElement === panel.element) document.exitFullscreen().catch(() => {});
  panel.element.remove();
  layout.releaseDashboard(panel.element, layoutKey(id));
}

function openPanel(placement) {
  closed.delete(placement.id);
  if (panels.has(placement.id)) return;
  const element = template.cloneNode(true);
  for (const child of element.querySelectorAll("[id]")) child.removeAttribute("id");
  element.removeAttribute("id");
  element.hidden = false;
  const heading = element.querySelector('[data-dash="heading"]');
  heading.id = `dashboard-heading-${placement.id}`;
  element.setAttribute("aria-labelledby", heading.id);
  const message = element.querySelector('[data-dash="message"]');
  const preview = element.querySelector('[data-dash="preview"]');
  const reload = element.querySelector('[data-dash="reload"]');
  const full = element.querySelector('[data-dash="fullscreen"]');
  const edit = element.querySelector('[data-dash="edit"]');
  element.querySelector('[data-dash="remove"]').remove();
  const sourceLink = document.createElement("a");
  sourceLink.className = "text-button";
  sourceLink.textContent = "Open source sheet";
  sourceLink.target = "_parent";
  edit.replaceWith(sourceLink);
  sourceLink.addEventListener("click", event => {
    if (!window.parent.phyzflowOpenDashboardSource || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    window.parent.phyzflowOpenDashboardSource(panel.placement.sheet_index);
  });

  let loading;
  let frameSession;
  function dispose() {
    loading?.abort();
    frameSession?.close();
  }

  function followPlacement(session) {
    const { signal } = session;
    let timer;
    let snapshot;
    signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
    async function poll() {
      try {
        const data = await fetchJSON(`placement-assets/${placement.id}/data`, "Could not load dashboard data.", { signal });
        if (signal.aborted) return;
        snapshot = data;
        message.textContent = "";
        session.send({ type: "dashboard-data", snapshot });
      } catch (error) {
        if (signal.aborted) return;
        message.textContent = error.message;
        if (error.status === 404) {
          panel.placement = { ...panel.placement, available: false };
          session.close();
          return;
        }
        if (snapshot) {
          session.send({ type: "dashboard-data", snapshot: {
            ...snapshot, run: null, timestamp: null, liveFrames: {}, liveMetrics: {},
          } });
        }
      }
      timer = setTimeout(poll, 500);
    }
    void poll();
  }

  async function load() {
    dispose();
    if (!panel.placement.available) {
      message.textContent = "Source panel unavailable.";
      return;
    }
    const controller = new AbortController();
    loading = controller;
    const { signal } = controller;
    message.textContent = "Loading dashboard…";
    try {
      const assets = await fetchJSON(`placement-assets/${placement.id}`, "Could not load the source panel.", { signal });
      if (signal.aborted) return;
      frameSession = mountDashboardFrame(preview, assets, heading.textContent, {
        subscribeOnly: true, onReady: followPlacement,
      });
      message.textContent = "";
    } catch (error) {
      if (!signal.aborted) message.textContent = error.message;
    }
  }
  const panel = {
    element,
    placement,
    update(next) {
      const changed = panel.placement.available !== next.available;
      panel.placement = next;
      heading.textContent = panelTitle(next);
      sourceLink.hidden = next.sheet_index === null;
      sourceLink.href = `../#flow-tab-${next.sheet_index}`;
      if (changed) void load();
    },
    dispose,
  };
  full.hidden = !document.fullscreenEnabled;
  full.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement === element) await document.exitFullscreen();
      else await element.requestFullscreen();
    } catch {
      message.textContent = "Could not change panel full screen mode.";
    }
  });
  reload.addEventListener("click", load);
  element.querySelector('[data-dash="close"]').addEventListener("click", () => {
    closePanel(placement.id);
    renderDrawer();
    menu.querySelector("summary").focus();
  });
  panels.set(placement.id, panel);
  panel.update(placement);
  graph.append(element);
  layout.revealDashboard(element, layoutKey(placement.id));
  void load();
}

document.addEventListener("fullscreenchange", () => {
  for (const { element } of panels.values()) {
    element.querySelector('[data-dash="fullscreen"]').textContent =
      document.fullscreenElement === element ? "Exit full screen" : "Full screen";
  }
});

function renderDrawer() {
  openAll.disabled = placements.length === panels.size;
  closeAll.disabled = panels.size === 0;
  if (!busy) showStatus(!placements.length ? "Add panels from this Workspace." : "");
  const placed = new Set(placements.map(sourceKey));
  const fragment = document.createDocumentFragment();
  for (const placement of placements) {
    const row = document.createElement("div");
    row.className = "workspace-placement-row";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "outline-node";
    toggle.textContent = panelTitle(placement);
    toggle.setAttribute("aria-pressed", String(panels.has(placement.id)));
    toggle.addEventListener("click", () => {
      if (panels.has(placement.id)) closePanel(placement.id);
      else openPanel(placement);
      renderDrawer();
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-button";
    remove.textContent = "Remove placement";
    remove.setAttribute("aria-label", `Remove placement: ${panelTitle(placement)}`);
    remove.disabled = busy;
    remove.addEventListener("click", () => refreshPanels({ action: "remove", id: placement.id }));
    row.append(toggle, remove);
    fragment.append(row);
  }
  for (const source of catalog) {
    if (placed.has(sourceKey(source))) continue;
    const add = document.createElement("button");
    add.type = "button";
    add.className = "outline-node";
    add.textContent = `Add ${panelTitle(source)}`;
    add.disabled = busy;
    add.addEventListener("click", () => refreshPanels({
      action: "add", sheet: source.sheet, scope: source.scope, id: source.id,
    }));
    fragment.append(add);
  }
  if (!fragment.childElementCount) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Create a panel in a sheet's Visualize drawer to add it here.";
    fragment.append(empty);
  }
  list.replaceChildren(fragment);
}

async function refreshPanels(change) {
  if (busy) return;
  busy = true;
  refresh.disabled = true;
  let errorMessage;
  try {
    const options = change && {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(change),
    };
    const result = await fetchJSON("placements", "Could not access dashboard placements.", options);
    placements = result.placements;
    for (const id of panels.keys()) {
      if (!placements.some(placement => placement.id === id)) closePanel(id);
    }
    for (const placement of placements) {
      const panel = panels.get(placement.id);
      if (panel) panel.update(placement);
      else if (!closed.has(placement.id)) openPanel(placement);
    }
    catalog = (await fetchJSON("panel-catalog", "Could not list source panels.")).panels;
  } catch (error) {
    errorMessage = error.message;
  } finally {
    busy = false;
    refresh.disabled = false;
    renderDrawer();
    if (errorMessage) showStatus(errorMessage);
  }
}

openAll.addEventListener("click", () => {
  for (const placement of placements) openPanel(placement);
  renderDrawer();
});
closeAll.addEventListener("click", () => {
  for (const id of panels.keys()) closePanel(id);
  renderDrawer();
});
refresh.addEventListener("click", () => refreshPanels());
function exit() {
  const dialog = window.frameElement?.closest("dialog");
  if (dialog) dialog.close();
  else window.location.assign("../");
}
back.addEventListener("click", exit);
window.addEventListener("keydown", event => {
  if (event.key === "Escape" && !document.fullscreenElement) exit();
});
window.addEventListener("pagehide", () => {
  for (const panel of panels.values()) panel.dispose();
});
showStatus("Loading dashboard panels…");
void refreshPanels();
