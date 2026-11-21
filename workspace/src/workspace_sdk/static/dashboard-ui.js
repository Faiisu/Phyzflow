import { initDashboardEditor } from "./dashboard-editor.js";

const primaryBoard = document.querySelector("#dashboard");

export const SHARED_DASHBOARD_THEME_CSS = `:root {
  color-scheme: dark;
  --bg: #141d2b;
  --surface: #171e2b;
  --panel: #202b3d;
  --dock: #25272d;
  --line: #3b4b63;
  --ink: #ecf0fa;
  --muted: #aebbd0;
  --accent: #86c7ff;
  --series-1: #86c7ff;
  --series-2: #ffbf78;
  --series-3: #aee4cf;
  --series-4: #d6a0fa;
  --state-idle: #aebbd0;
  --state-live: #86c7ff;
  --state-running: #ffd788;
  --state-succeeded: #aee4cf;
  --state-failed: #ffc0bd;
  --state-stopped: #aebbd0;
  --font-sans: "Instrument Sans", "Inter", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, monospace;
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --radius-1: 6px;
  --radius-2: 10px;
  color: var(--ink);
  background: var(--bg);
  font: 14px/1.4 var(--font-sans);
  font-synthesis: none;
}
:where(*) { box-sizing: border-box; }
:where(html, body) { margin: 0; max-width: 100%; overflow-x: hidden; }
:where(main) { padding: clamp(0.75rem, 3vw, 1rem) clamp(0.75rem, 3vw, 1.1rem) 0.85rem; min-width: 0; }
:where(h1) { margin: 0; font-size: 1.25rem; font-weight: 650; letter-spacing: -0.015em; line-height: 1.25; }
:where(h2, h3, h4) { margin: 0; font-weight: 600; line-height: 1.3; }
:where(p) { margin: 0.2rem 0 0.45rem; color: var(--muted); overflow-wrap: anywhere; }
:where(header) { display: flex; align-items: flex-start; flex-wrap: wrap; justify-content: space-between; gap: var(--space-2) var(--space-4); min-width: 0; }
:where(dl) { display: flex; flex-wrap: wrap; gap: var(--space-3) var(--space-4); margin: 0; min-width: 0; }
:where(dl > div) { min-width: 0; }
:where(dt) { color: var(--muted); font-size: 0.8rem; }
:where(dd) { margin: 0; font: 600 1.18rem/1.55 var(--mono); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
:where(output) { display: inline-block; min-width: 0; max-width: 100%; overflow-wrap: anywhere; }
:where(dd, output, .metric-value, code) { font-family: var(--mono); font-variant-numeric: tabular-nums; }
:where(code) { padding: 0.1rem 0.35rem; border-radius: 4px; background: var(--surface); border: 1px solid var(--line); font-size: 0.85em; }
:where(canvas) { display: block; width: 100%; max-width: 100%; height: clamp(10rem, 52vh, 17rem); margin-top: var(--space-2); border-radius: var(--radius-1); }
.reading { display: grid; gap: var(--space-1); min-width: 0; padding: var(--space-3); border: 1px solid var(--line); border-radius: var(--radius-1); background: var(--panel); }
.metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 8.5rem), 1fr)); gap: var(--space-2); margin-top: var(--space-3); min-width: 0; }
.metric-card { display: grid; gap: var(--space-1); min-width: 0; padding: var(--space-2) var(--space-3); border: 1px solid var(--line); border-radius: var(--radius-1); background: var(--surface); }
.metric-label { color: var(--muted); font-size: 0.78rem; overflow-wrap: anywhere; }
.metric-value { font-size: 1.05rem; font-weight: 600; color: var(--ink); overflow-wrap: anywhere; }
.chart-legend { display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-3); margin-top: var(--space-2); font-size: 0.78rem; color: var(--muted); }
.chart-legend-item { display: inline-flex; align-items: center; gap: 0.35rem; }
.chart-legend-swatch { width: 8px; height: 8px; border-radius: 50%; flex: none; }
[data-empty="true"], .muted { color: var(--muted); }
:where(button, input, select, textarea) { font: inherit; color: var(--ink); }
:where(button) { cursor: pointer; padding: 0.38rem 0.72rem; border: 1px solid var(--line); border-radius: var(--radius-1); background: var(--panel); transition: border-color 0.15s ease, background 0.15s ease; }
:where(button:not(:disabled):hover) { border-color: var(--accent); background: #27354b; }
:where(button:disabled) { opacity: 0.5; cursor: default; }
:where(input, select, textarea) { min-width: 0; max-width: 100%; padding: 0.36rem 0.52rem; border: 1px solid var(--line); border-radius: var(--radius-1); background: var(--surface); }
:where(button, input, select, textarea, a, [tabindex]):focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.state-badge { display: inline-flex; align-items: center; gap: 0.36rem; padding: 0.18rem 0.54rem; border-radius: 999px; border: 1px solid rgba(174, 187, 208, 0.25); background: rgba(174, 187, 208, 0.12); color: var(--state-idle); font: 600 0.72rem/1.3 var(--mono); white-space: nowrap; }
.state-badge::before { content: ""; flex: none; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.state-banner { padding: var(--space-2) var(--space-3); border: 1px solid var(--line); border-radius: var(--radius-1); background: var(--surface); font-size: 0.84rem; }
:is(.state-badge, .state-banner)[data-state="idle"], :is(.state-badge, .state-banner)[data-state="empty"], :is(.state-badge, .state-banner)[data-state="stopped"] { color: var(--state-idle); border-color: rgba(174, 187, 208, 0.25); background: rgba(174, 187, 208, 0.12); }
:is(.state-badge, .state-banner)[data-state="live"] { color: var(--state-live); border-color: rgba(134, 199, 255, 0.35); background: rgba(134, 199, 255, 0.13); }
:is(.state-badge, .state-banner)[data-state="running"] { color: var(--state-running); border-color: rgba(255, 215, 136, 0.32); background: rgba(255, 215, 136, 0.14); }
:is(.state-badge, .state-banner)[data-state="succeeded"] { color: var(--state-succeeded); border-color: rgba(174, 228, 207, 0.3); background: rgba(174, 228, 207, 0.13); }
:is(.state-badge, .state-banner)[data-state="failed"], :is(.state-badge, .state-banner)[data-state="error"] { color: var(--state-failed); border-color: rgba(255, 192, 189, 0.34); background: rgba(255, 192, 189, 0.14); }
@media (max-width: 420px) {
  :where(main) { padding: var(--space-2); }
  :where(header) { flex-direction: column; align-items: stretch; gap: var(--space-2); }
  :where(h1) { font-size: 1.08rem; }
  :where(dl) { gap: var(--space-2) var(--space-3); }
  :where(dd) { font-size: 1rem; }
  :where(.metric-grid) { grid-template-columns: minmax(0, 1fr); }
  :where(canvas) { height: clamp(8.5rem, 46vh, 14rem); }
}`;

const runFields = ["id", "status", "started_at", "finished_at", "duration_ms", "source_version"];
const nodeFields = ["id", "status", "started_at", "finished_at", "duration_ms", "blocked_by", "metrics", "error_type"];

function pick(record, keys) {
  return Object.fromEntries(keys.filter(key => Object.hasOwn(record, key)).map(key => [key, record[key]]));
}

function childBridge(version, workspace) {
  let snapshot;
  let request = 0;
  const listeners = new Set();
  const pending = new Map();
  const bridge = {
    version,
    subscribe(listener) {
      listeners.add(listener);
      if (snapshot) listener(snapshot);
      return () => listeners.delete(listener);
    },
  };
  if (!workspace) Object.assign(bridge, {
    openNode(nodeId) {
      parent.postMessage({ type: "dashboard-open-node", nodeId }, "*");
    },
    openPanel(panelId, nodeId = null) {
      parent.postMessage({ type: "dashboard-open-panel", panelId, nodeId }, "*");
    },
    setSetting(nodeId, name, value, mode = "next") {
      const id = ++request;
      return new Promise(resolve => {
        pending.set(id, resolve);
        parent.postMessage({ type: "dashboard-set-setting", id, nodeId, name, value, mode }, "*");
      });
    },
  });
  window.workspaceDashboard = Object.freeze(bridge);
  window.addEventListener("message", event => {
    if (event.source !== parent) return;
    if (event.data?.type === "dashboard-data") {
      snapshot = event.data.snapshot;
      for (const listener of listeners) listener(snapshot);
    } else if (event.data?.type === "dashboard-setting-result") {
      pending.get(event.data.id)?.(event.data.result);
      pending.delete(event.data.id);
    }
  });
}

function supportedBridgeVersions(assets) {
  if (!Object.hasOwn(assets, "bridge.json")) return [1];
  let versions;
  try {
    versions = JSON.parse(assets["bridge.json"])?.versions;
  } catch {
    throw new Error("Invalid bridge.json. Declare Dashboard Bridge versions as a non-empty list of positive integers.");
  }
  if (!Array.isArray(versions) || !versions.length || !versions.every(version => Number.isSafeInteger(version) && version > 0)) {
    throw new Error("Invalid bridge.json. Declare Dashboard Bridge versions as a non-empty list of positive integers.");
  }
  return versions;
}

function frameDocument(assets) {
  const themeCss = JSON.stringify(SHARED_DASHBOARD_THEME_CSS).replaceAll("<", "\\u003c");
  const css = JSON.stringify(assets["style.css"] ?? "").replaceAll("<", "\\u003c");
  const scripts = JSON.stringify(
    [assets["setting.js"], assets["dashboard.js"]].filter(Boolean),
  ).replaceAll("<", "\\u003c");
  return `<!doctype html>
    <meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; base-uri 'none'; form-action 'none'">
    <script>
    (${childBridge})(${assets.bridge_version}, ${assets.scope === "workspace"});
    (() => {
      const themeStyle = document.createElement("style");
      themeStyle.dataset.phyzflowTheme = "true";
      themeStyle.textContent = ${themeCss};
      document.head.append(themeStyle);
      const style = document.createElement("style");
      style.textContent = ${css};
      document.head.append(style);
      window.addEventListener("DOMContentLoaded", () => {
        for (const source of ${scripts}) {
          const script = document.createElement("script");
          script.textContent = source;
          document.body.append(script);
        }
        parent.postMessage({ type: "dashboard-ready" }, "*");
      }, { once: true });
    })();
    </script>
    ${assets["index.html"]}`;
}

export function createDashboardFrame(assets, title) {
  const supported = supportedBridgeVersions(assets);
  if (!supported.includes(assets.bridge_version)) {
    throw new Error(`Incompatible Dashboard Bridge. Observed version ${assets.bridge_version}. Supported versions ${supported.join(", ")}.`);
  }
  const frame = document.createElement("iframe");
  frame.title = title;
  frame.setAttribute("sandbox", "allow-scripts allow-forms");
  frame.referrerPolicy = "no-referrer";
  frame.srcdoc = frameDocument(assets);
  return frame;
}

export async function fetchJSON(path, errorMessage, options) {
  const response = await fetch(path, options);
  const isJSON = response.headers.get("Content-Type")?.startsWith("application/json");
  const data = isJSON ? await response.json() : null;
  if (!response.ok) throw new Error(data?.error ?? errorMessage);
  return data;
}

export function initDashboards(project, revealBoard, releaseBoard = () => {}, setVisualizeModeLayout = () => {}) {
  const graph = project.steps.map(step => pick(step, ["id", "dependencies"]));
  let liveRun = null;
  let stopped = false;
  let cloneCounter = 0;
  const previews = new Map();
  const frames = new Map();
  const metrics = new Map();
  const settingChanges = new Map();
  const activePanels = new Set();
  let availablePanels = [];
  let listVersion = 0;
  const scopes = [
    { nodeId: null, key: "workflow", title: "Workflow" },
    ...graph.map((node, index) => ({ nodeId: node.id, key: String(index), title: node.id })),
  ];
  const dashboardEditor = initDashboardEditor({
    scopes,
    request(nodeId, data) {
      const path = `dashboard-source/${scopeKey(nodeId)}`;
      return fetchJSON(path, "Could not access dashboard source.", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
    },
    async onCreate(nodeId, panelId) {
      await refreshVisualizeList();
      const entry = availablePanels.find(item => item.nodeId === nodeId && item.panelId === panelId);
      openSinglePanel(nodeId, panelId, entry?.label);
    },
    onSave({ nodeId, panelId }) {
      findPanel(nodeId, panelId)?.loadDashboard();
      refreshVisualizeList();
    },
    onRemove({ nodeId, panelId }) {
      findPanel(nodeId, panelId)?.close();
      refreshVisualizeList();
      document.querySelector('[aria-controls="panel-visualize"]').focus();
    },
  });

  function layoutKey(scope, panelId) {
    return JSON.stringify([scope, panelId || "default"]);
  }

  function scopeKey(scope) {
    return scope === null ? "workflow" : graph.findIndex(node => node.id === scope);
  }

  function rememberChange(nodeId, change) {
    settingChanges.set(change.id, { ...change, nodeId });
    if (settingChanges.size > 64) settingChanges.delete(settingChanges.keys().next().value);
  }

  function createPanelElement() {
    if (primaryBoard.hidden && !activePanels.has(primaryBoard._panelInstance)) {
      return { element: primaryBoard, isPrimary: true };
    }
    const id = ++cloneCounter;
    const element = primaryBoard.cloneNode(true);
    element.removeAttribute("id");
    for (const child of element.querySelectorAll("[id]")) child.removeAttribute("id");
    const headingId = `dashboard-heading-${id}`;
    element.querySelector('[data-dash="heading"]').id = headingId;
    element.setAttribute("aria-labelledby", headingId);
    releaseBoard(element, null);
    primaryBoard.parentElement.append(element);
    return { element, isPrimary: false };
  }

  function createPanel() {
    const { element, isPrimary } = createPanelElement();
    const heading = element.querySelector('[data-dash="heading"]');
    const preview = element.querySelector('[data-dash="preview"]');
    const message = element.querySelector('[data-dash="message"]');
    const reload = element.querySelector('[data-dash="reload"]');
    const fullscreen = element.querySelector('[data-dash="fullscreen"]');
    const closeBtn = element.querySelector('[data-dash="close"]');

    const panel = {
      element,
      isPrimary,
      scope: null,
      panelId: "default",
      panelLabel: "",
      frame: null,
      ready: false,
      loadVersion: 0,
      runVersion: 0,
      savedRun: null,
      following: true,
      settings: {},
      opener: null,
    };
    if (isPrimary) primaryBoard._panelInstance = panel;

    function updateHeading() {
      const base = panel.scope === null ? "Workflow dashboard" : `${panel.scope} dashboard`;
      heading.textContent = panel.panelId && panel.panelId !== "default"
        ? `${base} · ${panel.panelLabel || panel.panelId}`
        : base;
    }

    panel.sendSnapshot = function sendSnapshot() {
      if (!panel.ready || !panel.frame?.contentWindow) return;
      const run = panel.following ? liveRun : panel.savedRun;
      const inScope = id => panel.scope === null || id === panel.scope;
      panel.frame.contentWindow.postMessage({
        type: "dashboard-data",
        snapshot: {
          scope: panel.scope === null ? "workflow" : "node",
          nodeId: panel.scope,
          panelId: panel.panelId,
          mode: panel.following ? "live" : "saved",
          graph: graph.filter(node => inScope(node.id)),
          run: run && {
            ...pick(run, runFields),
            steps: run.steps.filter(node => inScope(node.id)).map(node => pick(node, nodeFields)),
          },
          previews: panel.following ? Object.fromEntries([...previews].filter(([id]) => inScope(id))) : {},
          liveFrames: panel.following ? Object.fromEntries([...frames].filter(([id]) => inScope(id))) : {},
          liveMetrics: panel.following ? Object.fromEntries([...metrics].filter(([id]) => inScope(id))) : {},
          settings: panel.settings,
          settingChanges: panel.following ? [...settingChanges.values()].filter(change => inScope(change.nodeId)) : [],
        },
      }, "*");
    };

    panel.loadDashboard = async function loadDashboard() {
      const version = ++panel.loadVersion;
      panel.ready = false;
      panel.frame = null;
      preview.replaceChildren();
      message.textContent = "Loading dashboard…";
      try {
        const key = scopeKey(panel.scope);
        const panelPath = encodeURIComponent(panel.panelId);
        const assetPath = panel.panelId && panel.panelId !== "default" ? `dashboard-assets/${key}/${panelPath}` : `dashboard-assets/${key}`;
        const assets = await fetchJSON(assetPath, "Could not load the dashboard.");
        if (version !== panel.loadVersion || element.hidden) return;
        const frame = createDashboardFrame(assets, heading.textContent);
        panel.frame = frame;
        preview.replaceChildren(frame);
        message.textContent = "";
        panel.loadSettings(version);
      } catch (error) {
        if (version === panel.loadVersion) message.textContent = error.message;
      }
    };

    panel.loadSettings = async function loadSettings(version) {
      if (!panel.frame) return;
      try {
        const current = await fetchJSON(`dashboard-settings/${scopeKey(panel.scope)}`, "Could not load dashboard settings.");
        if (version !== panel.loadVersion) return;
        panel.settings = current;
        panel.sendSnapshot();
      } catch (error) {
        if (version === panel.loadVersion) message.textContent = error.message;
      }
    };

    panel.changeSetting = async function changeSetting(data, sender) {
      const { id, nodeId, name, mode, value } = data;
      if (!Number.isSafeInteger(id)) return;
      const runId = mode === "live" && panel.following ? liveRun?.id : null;
      let result;
      try {
        result = await fetchJSON(`dashboard-settings/${scopeKey(panel.scope)}`, "Could not change this setting.", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nodeId, name, mode, value, runId }),
        });
        if (result.status === "queued" && liveRun?.id === runId) {
          if (!settingChanges.has(result.id)) rememberChange(nodeId, result);
          result = settingChanges.get(result.id);
        }
        if (result.status === "saved") {
          await Promise.all([...activePanels].map(item => item.loadSettings(item.loadVersion)));
        }
      } catch (error) {
        result = { status: "rejected", message: error.message };
      }
      if (sender !== panel.frame?.contentWindow) return;
      sender.postMessage({ type: "dashboard-setting-result", id, result }, "*");
      panel.sendSnapshot();
    };

    panel.setScope = function setScope(nodeId, panelId = "default", panelLabel = "") {
      if (!element.hidden && activePanels.has(panel)) {
        releaseBoard(element, layoutKey(panel.scope, panel.panelId));
      }
      panel.scope = nodeId;
      panel.panelId = panelId;
      panel.panelLabel = panelLabel;
      panel.runVersion++;
      panel.savedRun = null;
      panel.following = true;
      panel.settings = {};
      updateHeading();
      if (element.hidden) panel.opener = document.activeElement;
      element.hidden = false;
      activePanels.add(panel);
      revealBoard(element, layoutKey(nodeId, panelId));
      panel.loadDashboard();
      panel.sendSnapshot();
    };

    panel.close = function close() {
      if (element.hidden && !activePanels.has(panel)) return;
      releaseBoard(element, layoutKey(panel.scope, panel.panelId));
      if (document.fullscreenElement === element) document.exitFullscreen().catch(() => {});
      panel.loadVersion++;
      panel.runVersion++;
      panel.ready = false;
      panel.frame = null;
      preview.replaceChildren();
      activePanels.delete(panel);
      if (panel.isPrimary) {
        element.hidden = true;
      } else {
        element.remove();
      }
      syncVisualizeDrawer();
    };

    const onFullscreenChange = () => {
      if (!element.isConnected) {
        document.removeEventListener("fullscreenchange", onFullscreenChange);
        return;
      }
      fullscreen.textContent = document.fullscreenElement === element ? "Exit full screen" : "Full screen";
    };
    document.addEventListener("fullscreenchange", onFullscreenChange);

    element.querySelector('[data-dash="edit"]').onclick = () => dashboardEditor.edit(panel);
    element.querySelector('[data-dash="remove"]').onclick = () => dashboardEditor.remove(panel);
    closeBtn.addEventListener("click", () => {
      const opener = panel.opener;
      panel.close();
      opener?.focus({ preventScroll: true });
    });

    fullscreen.hidden = !document.fullscreenEnabled;
    fullscreen.addEventListener("click", async () => {
      try {
        if (document.fullscreenElement === element) await document.exitFullscreen();
        else await element.requestFullscreen();
      } catch {
        message.textContent = "Could not change dashboard full screen mode.";
      }
    });

    reload.addEventListener("click", () => {
      panel.loadDashboard();
      refreshVisualizeList();
    });

    return panel;
  }

  function findPanel(nodeId, panelId) {
    return [...activePanels].find(p => p.scope === nodeId && p.panelId === panelId);
  }

  function syncVisualizeDrawer() {
    const list = document.querySelector("#visualize-list");
    if (list) {
      for (const button of list.querySelectorAll("button[data-vis-panel]")) {
        const nodeId = button.dataset.visNode === "" ? null : button.dataset.visNode;
        const panelId = button.dataset.visPanel;
        button.setAttribute("aria-pressed", String(Boolean(findPanel(nodeId, panelId))));
      }
    }
    const openAllBtn = document.querySelector("#visualize-open-all");
    const closeAllBtn = document.querySelector("#visualize-close-all");
    const enterVisBtn = document.querySelector("#enter-visualize-mode");
    const graphEl = document.querySelector("#graph");
    if (graphEl) graphEl.dataset.panelCount = String(activePanels.size);
    if (openAllBtn) {
      openAllBtn.disabled = !availablePanels.length || availablePanels.every(item => findPanel(item.nodeId, item.panelId));
    }
    if (closeAllBtn) {
      closeAllBtn.disabled = activePanels.size === 0;
    }
    if (enterVisBtn) {
      enterVisBtn.disabled = availablePanels.length === 0 && activePanels.size === 0;
    }
    if (activePanels.size === 0 && document.body.classList.contains("is-visualize-mode")) {
      setVisualizeMode(false);
    }
  }

  function openSinglePanel(nodeId, panelId = "default", panelLabel = "") {
    if (stopped) return;
    const existing = findPanel(nodeId, panelId);
    if (existing) {
      revealBoard(existing.element, layoutKey(nodeId, panelId));
      syncVisualizeDrawer();
      return existing;
    }
    const panel = primaryBoard.hidden && primaryBoard._panelInstance || createPanel();
    panel.setScope(nodeId, panelId, panelLabel);
    syncVisualizeDrawer();
    return panel;
  }

  async function refreshVisualizeList() {
    const list = document.querySelector("#visualize-list");
    if (!list) return;
    const version = ++listVersion;
    const results = await Promise.allSettled(
      scopes.map(async item => {
        const path = `dashboard-panels/${item.key}`;
        const data = await fetchJSON(path, "Could not list panels.");
        return { ...item, panels: data.panels ?? [] };
      }),
    );
    if (version !== listVersion) return;
    const groups = results
      .filter(res => res.status === "fulfilled" && res.value.panels.length > 0)
      .map(res => res.value);

    availablePanels = groups.flatMap(group =>
      group.panels.map(entry => ({ nodeId: group.nodeId, panelId: entry.id, label: entry.label })),
    );

    if (!groups.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "No dashboards found in this flow.";
      list.replaceChildren(empty);
      syncVisualizeDrawer();
      return;
    }

    const fragment = document.createDocumentFragment();
    let itemIndex = 0;
    for (const group of groups) {
      const heading = document.createElement("div");
      heading.className = "visualize-group-title";
      heading.textContent = group.title;
      fragment.append(heading);

      for (const entry of group.panels) {
        itemIndex += 1;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "outline-node";
        btn.dataset.visNode = group.nodeId ?? "";
        btn.dataset.visPanel = entry.id;
        btn.setAttribute("aria-pressed", String(Boolean(findPanel(group.nodeId, entry.id))));

        const idx = document.createElement("span");
        idx.className = "node-index";
        idx.textContent = String(itemIndex).padStart(2, "0");

        const label = document.createElement("span");
        label.textContent = entry.label || entry.id;

        btn.append(idx, label);
        btn.addEventListener("click", () => {
          const current = findPanel(group.nodeId, entry.id);
          if (current) {
            current.close();
          } else {
            openSinglePanel(group.nodeId, entry.id, entry.label);
          }
        });
        fragment.append(btn);
      }
    }
    list.replaceChildren(fragment);
    syncVisualizeDrawer();
  }

  async function open(nodeId, panelId = null) {
    if (stopped) return;
    if (panelId !== null) {
      openSinglePanel(nodeId, panelId, "");
      return;
    }
    let panels = [];
    try {
      const data = await fetchJSON(`dashboard-panels/${scopeKey(nodeId)}`, "Could not list dashboard panels.");
      panels = data.panels ?? [];
    } catch {
      panels = [];
    }
    if (panels.length <= 1) {
      const first = panels[0] ?? { id: "default", label: "" };
      openSinglePanel(nodeId, first.id, first.label);
      return;
    }
    for (const entry of panels) {
      openSinglePanel(nodeId, entry.id, entry.label);
    }
  }

  const visualizeDrawer = document.querySelector("#panel-visualize");
  const exitVisualizeBtn = document.querySelector("#exit-visualize-mode");

  function setVisualizeMode(active) {
    if (active && activePanels.size === 0) {
      for (const item of availablePanels) {
        if (!findPanel(item.nodeId, item.panelId)) {
          openSinglePanel(item.nodeId, item.panelId, item.label);
        }
      }
      if (activePanels.size === 0) return;
    }
    const currentlyActive = document.body.classList.contains("is-visualize-mode");
    if (currentlyActive === active) return;
    document.body.classList.toggle("is-visualize-mode", active);
    if (exitVisualizeBtn) exitVisualizeBtn.hidden = !active;
    setVisualizeModeLayout(active);
  }

  function openAll() {
    for (const item of availablePanels) {
      if (!findPanel(item.nodeId, item.panelId)) {
        openSinglePanel(item.nodeId, item.panelId, item.label);
      }
    }
  }
  document.querySelector("#visualize-open-all")?.addEventListener("click", openAll);
  document.querySelector("#visualize-close-all")?.addEventListener("click", () => {
    for (const panel of [...activePanels]) panel.close();
  });
  document.querySelector("#enter-visualize-mode")?.addEventListener("click", () => {
    setVisualizeMode(true);
  });
  exitVisualizeBtn?.addEventListener("click", () => {
    setVisualizeMode(false);
  });
  window.addEventListener("keydown", event => {
    if (event.key === "Escape" && !document.querySelector("#dashboard-editor").open
      && document.body.classList.contains("is-visualize-mode") && !document.fullscreenElement) {
      setVisualizeMode(false);
    }
  });

  if (visualizeDrawer) {
    const refreshVisibleDrawer = () => {
      if (!visualizeDrawer.hidden) refreshVisualizeList();
    };
    new MutationObserver(refreshVisibleDrawer).observe(visualizeDrawer, { attributeFilter: ["hidden"] });
    refreshVisibleDrawer();
  }
  document.querySelector("#visualize-refresh")?.addEventListener("click", () => {
    refreshVisualizeList();
  });
  let wasLocked = Boolean(project.edit_lock?.locked);
  let lastEditGeneration = Number(project.edit_lock?.generation ?? 0);
  window.addEventListener("workspace-edit-lock", event => {
    const editLock = event.detail?.editLock;
    const locked = Boolean(editLock?.locked);
    const generation = Number(editLock?.generation ?? lastEditGeneration);
    if (!locked && (wasLocked || generation > lastEditGeneration) && visualizeDrawer && !visualizeDrawer.hidden) {
      refreshVisualizeList();
    }
    wasLocked = locked;
    lastEditGeneration = Math.max(lastEditGeneration, generation);
  });

  window.addEventListener("message", event => {
    if (event.origin !== "null") return;
    const panel = [...activePanels].find(p => p.frame?.contentWindow === event.source);
    if (!panel || panel.element.hidden) return;
    const data = event.data;
    if (data?.type === "dashboard-ready") {
      panel.ready = true;
      panel.sendSnapshot();
    } else if (data?.type === "dashboard-open-node" && panel.scope === null && graph.some(node => node.id === data.nodeId)) {
      panel.setScope(data.nodeId, "default", "");
    } else if (data?.type === "dashboard-open-panel" && typeof data.panelId === "string") {
      const targetNode = data.nodeId ?? null;
      if (targetNode === null || graph.some(node => node.id === targetNode)) {
        openSinglePanel(targetNode, data.panelId, "");
      }
    } else if (data?.type === "dashboard-set-setting") {
      panel.changeSetting(data, event.source);
    }
  });

  window.addEventListener("workspace-stop", event => {
    stopped = event.detail.phase === "stopped";
    if (stopped) {
      frames.clear();
      for (const panel of [...activePanels]) panel.close();
    }
  });

  function track(event) {
    if (event.result || event.kind === "console") {
      return;
    }
    if (!event.run_id) {
      if (event.error && liveRun?.status === "running") {
        liveRun.status = "failed";
        frames.clear();
        for (const panel of activePanels) {
          if (panel.following) panel.sendSnapshot();
        }
      }
      return;
    }
    const { step_id: id, status, time } = event;
    if (id === undefined && status === "running") {
      liveRun = { id: event.run_id, status, started_at: time, steps: [] };
      previews.clear();
      frames.clear();
      metrics.clear();
      settingChanges.clear();
      for (const panel of activePanels) {
        panel.runVersion++;
        panel.following = true;
      }
    } else if (liveRun?.id === event.run_id) {
      if (id === undefined) {
        liveRun.status = status;
        frames.clear();
        liveRun.finished_at = time;
        liveRun.duration_ms = Date.parse(time) - Date.parse(liveRun.started_at);
      } else if (event.output_preview !== undefined) {
        previews.set(id, JSON.parse(event.output_preview));
      } else if (event.live_frame !== undefined) {
        frames.set(id, `data:image/jpeg;base64,${event.live_frame}`);
      } else if (event.live_metrics) {
        const groups = { ...metrics.get(id), ...event.live_metrics };
        metrics.set(id, Object.fromEntries(Object.entries(groups).slice(-8)));
      } else if (event.setting_change) {
        rememberChange(id, { ...event.setting_change, runId: event.run_id, time: event.time });
      } else {
        let step = liveRun.steps.find(node => node.id === id);
        if (!step) {
          step = { id };
          liveRun.steps.push(step);
        }
        Object.assign(step, pick(event, nodeFields));
        if (status === "running") step.started_at = time;
        else if (status !== "skipped") step.finished_at = time;
      }
    }
    for (const panel of activePanels) {
      if (panel.following) panel.sendSnapshot();
    }
  }

  return { open, track };
}
