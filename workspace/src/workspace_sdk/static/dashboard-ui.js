const primaryBoard = document.querySelector("#dashboard");

const runFields = ["id", "status", "started_at", "finished_at", "duration_ms", "source_version"];
const nodeFields = ["id", "status", "started_at", "finished_at", "duration_ms", "blocked_by", "metrics", "error_type"];

function pick(record, keys) {
  return Object.fromEntries(keys.filter(key => Object.hasOwn(record, key)).map(key => [key, record[key]]));
}

function childBridge() {
  let snapshot;
  let request = 0;
  const listeners = new Set();
  const pending = new Map();
  window.workspaceDashboard = Object.freeze({
    subscribe(listener) {
      listeners.add(listener);
      if (snapshot) listener(snapshot);
      return () => listeners.delete(listener);
    },
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

function frameDocument(assets) {
  const css = JSON.stringify(assets["style.css"] ?? "").replaceAll("<", "\\u003c");
  const scripts = JSON.stringify(
    [assets["setting.js"], assets["dashboard.js"]].filter(Boolean),
  ).replaceAll("<", "\\u003c");
  return `<!doctype html>
    <meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; base-uri 'none'; form-action 'none'">
    <script>
    (${childBridge})();
    (() => {
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

async function fetchJSON(path, errorMessage, options) {
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

  function layoutKey(scope, panelId) {
    return `${scope ?? "workflow"}::${panelId || "default"}`;
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
      listVersion: 0,
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
        const assetPath = panel.panelId && panel.panelId !== "default"
          ? `dashboard-assets/${key}/${encodeURIComponent(panel.panelId)}`
          : `dashboard-assets/${key}`;
        const assets = await fetchJSON(assetPath, "Could not load the dashboard.");
        if (version !== panel.loadVersion || element.hidden) return;
        const frame = document.createElement("iframe");
        frame.title = heading.textContent;
        // The CSP blocks native submissions. The sandbox permits submit handlers to run.
        frame.setAttribute("sandbox", "allow-scripts allow-forms");
        frame.referrerPolicy = "no-referrer";
        frame.srcdoc = frameDocument(assets);
        panel.frame = frame;
        preview.replaceChildren(frame);
        message.textContent = "";
      } catch (error) {
        if (version === panel.loadVersion) message.textContent = error.message;
      }
    };

    panel.loadSettings = async function loadSettings(version) {
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
      panel.loadSettings(panel.loadVersion);
      panel.sendSnapshot();
    };

    panel.close = function close() {
      if (element.hidden && !activePanels.has(panel)) return;
      releaseBoard(element, layoutKey(panel.scope, panel.panelId));
      if (document.fullscreenElement === element) document.exitFullscreen().catch(() => {});
      panel.loadVersion++;
      panel.listVersion++;
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
      panel.loadSettings(panel.loadVersion);
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
    const panel = createPanel();
    panel.setScope(nodeId, panelId, panelLabel);
    syncVisualizeDrawer();
    return panel;
  }

  async function refreshVisualizeList() {
    const list = document.querySelector("#visualize-list");
    if (!list) return;
    const scopes = [
      { nodeId: null, key: "workflow", title: "Workflow" },
      ...graph.map((node, index) => ({ nodeId: node.id, key: String(index), title: node.id })),
    ];
    const results = await Promise.allSettled(
      scopes.map(async item => {
        const data = await fetchJSON(`dashboard-panels/${item.key}`, "Could not list panels.");
        return { ...item, panels: data.panels ?? [] };
      }),
    );
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

  async function open(nodeId, preferred = liveRun?.status === "running" ? "live" : null, panelId = null) {
    if (stopped) return;
    if (panelId !== null) {
      openSinglePanel(nodeId, panelId, "", preferred);
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
      openSinglePanel(nodeId, first.id, first.label, preferred);
      return;
    }
    for (const entry of panels) {
      openSinglePanel(nodeId, entry.id, entry.label, preferred);
    }
  }

  const visualizeDrawer = document.querySelector("#panel-visualize");
  const visualizeToggle = document.querySelector('#panel-switcher button[aria-controls="panel-visualize"]');
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

  document.querySelector("#visualize-open-all")?.addEventListener("click", () => {
    for (const item of availablePanels) {
      if (!findPanel(item.nodeId, item.panelId)) {
        openSinglePanel(item.nodeId, item.panelId, item.label);
      }
    }
  });
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
    if (event.key === "Escape" && document.body.classList.contains("is-visualize-mode") && !document.fullscreenElement) {
      setVisualizeMode(false);
    }
  });

  if (visualizeDrawer && !visualizeDrawer.hidden) {
    refreshVisualizeList();
  }
  visualizeToggle?.addEventListener("click", () => {
    if (visualizeDrawer && !visualizeDrawer.hidden) refreshVisualizeList();
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
    if (event.result) {
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
