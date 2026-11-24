import { fetchJSON, mountDashboardFrame } from "./dashboard-bridge.js";
import { initDashboardEditor } from "./dashboard-editor.js";

const primaryBoard = document.querySelector("#dashboard");

const runFields = ["id", "status", "started_at", "finished_at", "duration_ms", "source_version"];
const nodeFields = ["id", "status", "started_at", "finished_at", "duration_ms", "iterations", "blocked_by", "metrics", "error_type"];

function pick(record, keys) {
  return Object.fromEntries(keys.filter(key => Object.hasOwn(record, key)).map(key => [key, record[key]]));
}

export function initDashboards(project, revealBoard, releaseBoard = () => {}, setVisualizeModeLayout = () => {}) {
  const graph = project.steps.map(step => pick(step, ["id", "dependencies", "loop_dependencies"]));
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
  const acquireEdit = window.parent !== window && window.parent.phyzflowAcquireEditLock;
  const dashboardEditor = initDashboardEditor({
    scopes,
    acquireEdit: acquireEdit ? () => acquireEdit(window) : null,
    releaseEdit: options => window.parent.phyzflowReleaseEditLock(window, options),
    request(nodeId, data, editSessionId) {
      const path = `dashboard-source/${scopeKey(nodeId)}`;
      const headers = { "Content-Type": "application/json" };
      if (editSessionId) headers["X-Phyzflow-Edit-Session"] = editSessionId;
      return fetchJSON(path, "Could not access dashboard source.", {
        method: "POST",
        headers,
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
    let loading;
    let frameSession;

    const panel = {
      element,
      isPrimary,
      scope: null,
      panelId: "default",
      panelLabel: "",
      settings: {},
      opener: null,
    };
    if (isPrimary) primaryBoard._panelInstance = panel;

    function closeFrame() {
      loading?.abort();
      frameSession?.close();
      frameSession = null;
    }

    function updateHeading() {
      const base = panel.scope === null ? "Workflow dashboard" : `${panel.scope} dashboard`;
      heading.textContent = panel.panelId && panel.panelId !== "default"
        ? `${base} · ${panel.panelLabel || panel.panelId}`
        : base;
    }

    panel.sendSnapshot = function sendSnapshot() {
      if (!frameSession) return;
      const inScope = id => panel.scope === null || id === panel.scope;
      frameSession.send({
        type: "dashboard-data",
        snapshot: {
          scope: panel.scope === null ? "workflow" : "node",
          nodeId: panel.scope,
          panelId: panel.panelId,
          mode: "live",
          graph: graph.filter(node => inScope(node.id)),
          run: liveRun && {
            ...pick(liveRun, runFields),
            steps: liveRun.steps.filter(node => inScope(node.id)).map(node => pick(node, nodeFields)),
          },
          previews: Object.fromEntries([...previews].filter(([id]) => inScope(id))),
          liveFrames: Object.fromEntries([...frames].filter(([id]) => inScope(id))),
          liveMetrics: Object.fromEntries([...metrics].filter(([id]) => inScope(id))),
          settings: panel.settings,
          settingChanges: [...settingChanges.values()].filter(change => inScope(change.nodeId)),
        },
      });
    };

    panel.loadDashboard = async function loadDashboard() {
      closeFrame();
      loading = new AbortController();
      const { signal } = loading;
      message.textContent = "Loading dashboard…";
      try {
        const key = scopeKey(panel.scope);
        const panelPath = encodeURIComponent(panel.panelId);
        const assetPath = panel.panelId && panel.panelId !== "default" ? `dashboard-assets/${key}/${panelPath}` : `dashboard-assets/${key}`;
        const assets = await fetchJSON(assetPath, "Could not load the dashboard.", { signal });
        if (signal.aborted) return;
        frameSession = mountDashboardFrame(preview, assets, heading.textContent, {
          onReady: panel.sendSnapshot,
          fileURL: panel.scope === null ? `dashboard-files/${panelPath || "default"}` : null,
          onMessage: handleDashboardMessage,
        });
        message.textContent = "";
        panel.loadSettings();
      } catch (error) {
        if (!signal.aborted) message.textContent = error.message;
      }
    };

    panel.loadSettings = async function loadSettings() {
      if (!frameSession) return;
      const { signal } = frameSession;
      try {
        const current = await fetchJSON(`dashboard-settings/${scopeKey(panel.scope)}`, "Could not load dashboard settings.", { signal });
        if (signal.aborted) return;
        panel.settings = current;
        panel.sendSnapshot();
      } catch (error) {
        if (!signal.aborted) message.textContent = error.message;
      }
    };

    async function changeSetting(data, session) {
      const { id, nodeId, name, mode, value } = data;
      if (!Number.isSafeInteger(id)) return;
      const runId = mode === "live" ? liveRun?.id : null;
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
          await Promise.all([...activePanels].map(item => item.loadSettings()));
        }
      } catch (error) {
        result = { status: "rejected", message: error.message };
      }
      if (session.signal.aborted) return;
      session.send({ type: "dashboard-setting-result", id, result });
      panel.sendSnapshot();
    }

    function handleDashboardMessage(data, session) {
      if (data?.type === "dashboard-open-node" && panel.scope === null && graph.some(node => node.id === data.nodeId)) {
        panel.setScope(data.nodeId, "default", "");
      } else if (data?.type === "dashboard-open-panel" && typeof data.panelId === "string") {
        const targetNode = data.nodeId ?? null;
        if (targetNode === null || graph.some(node => node.id === targetNode)) {
          openSinglePanel(targetNode, data.panelId, "");
        }
      } else if (data?.type === "dashboard-set-setting") {
        changeSetting(data, session);
      }
    }

    panel.setScope = function setScope(nodeId, panelId = "default", panelLabel = "") {
      if (!element.hidden && activePanels.has(panel)) {
        releaseBoard(element, layoutKey(panel.scope, panel.panelId));
      }
      panel.scope = nodeId;
      panel.panelId = panelId;
      panel.panelLabel = panelLabel;
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
      closeFrame();
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

  function renderVisualizeScopes(scopeMap) {
    const list = document.querySelector("#visualize-list");
    if (!list) return;
    const groups = scopes
      .map(item => ({ ...item, panels: Array.isArray(scopeMap?.[item.key]) ? scopeMap[item.key] : [] }))
      .filter(group => group.panels.length > 0);

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

  async function refreshVisualizeList() {
    const list = document.querySelector("#visualize-list");
    if (!list) return;
    const version = ++listVersion;
    try {
      const data = await fetchJSON("dashboard-panels", "Could not list panels.");
      if (version !== listVersion) return;
      renderVisualizeScopes(data?.scopes ?? {});
    } catch {
      const results = await Promise.allSettled(
        scopes.map(async item => {
          const path = `dashboard-panels/${item.key}`;
          const data = await fetchJSON(path, "Could not list panels.");
          return [item.key, data.panels ?? []];
        }),
      );
      if (version !== listVersion) return;
      const fallbackMap = Object.fromEntries(
        results.filter(res => res.status === "fulfilled").map(res => res.value),
      );
      renderVisualizeScopes(fallbackMap);
    }
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

  function refreshVisibleDrawer() {
    if (!visualizeDrawer.hidden) void refreshVisualizeList();
  }

  function loadPanels() {
    if (!visualizeDrawer) return;
    if (project.dashboard_panels && typeof project.dashboard_panels === "object") {
      renderVisualizeScopes(project.dashboard_panels);
    } else {
      refreshVisibleDrawer();
    }
  }
  if (visualizeDrawer) {
    new MutationObserver(refreshVisibleDrawer).observe(visualizeDrawer, { attributeFilter: ["hidden"] });
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
          panel.sendSnapshot();
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
        if (event.iteration != null) step.iterations = Math.max(step.iterations ?? 0, event.iteration);
        if (status === "running") step.started_at ??= time;
        else if (status !== "skipped") step.finished_at = time;
      }
    }
    for (const panel of activePanels) {
      panel.sendSnapshot();
    }
  }

  return { loadPanels, open, track };
}
