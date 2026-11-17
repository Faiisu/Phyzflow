const board = document.querySelector("#dashboard");
const heading = document.querySelector("#dashboard-heading");
const runSelect = document.querySelector("#dashboard-run");
const preview = document.querySelector("#dashboard-preview");
const message = document.querySelector("#dashboard-message");
const reload = document.querySelector("#reload-dashboard");
const fullscreen = document.querySelector("#fullscreen-dashboard");

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

export function initDashboards(project, revealBoard) {
  const graph = project.steps.map(step => pick(step, ["id", "dependencies"]));
  const workflowButton = document.querySelector("#visualize-workflow");
  let scope = null;
  let frame;
  let ready = false;
  let loadVersion = 0;
  let listVersion = 0;
  let runVersion = 0;
  let savedRun = null;
  let liveRun = null;
  let following = false;
  let stopped = false;
  let opener;
  let settings = {};
  const previews = new Map();
  const frames = new Map();
  const metrics = new Map();
  const settingChanges = new Map();

  function scopeKey() {
    return scope === null ? "workflow" : graph.findIndex(node => node.id === scope);
  }

  function sendSnapshot() {
    if (!ready) return;
    const run = following ? liveRun : savedRun;
    const inScope = id => scope === null || id === scope;
    frame.contentWindow.postMessage({
      type: "dashboard-data",
      snapshot: {
        scope: scope === null ? "workflow" : "node",
        nodeId: scope,
        mode: following ? "live" : "saved",
        graph: graph.filter(node => inScope(node.id)),
        run: run && {
          ...pick(run, runFields),
          steps: run.steps.filter(node => inScope(node.id)).map(node => pick(node, nodeFields)),
        },
        previews: following ? Object.fromEntries([...previews].filter(([id]) => inScope(id))) : {},
        liveFrames: following ? Object.fromEntries([...frames].filter(([id]) => inScope(id))) : {},
        liveMetrics: following ? Object.fromEntries([...metrics].filter(([id]) => inScope(id))) : {},
        settings,
        settingChanges: following ? [...settingChanges.values()].filter(change => inScope(change.nodeId)) : [],
      },
    }, "*");
  }

  async function loadDashboard() {
    const version = ++loadVersion;
    ready = false;
    frame = null;
    preview.replaceChildren();
    message.textContent = "Loading dashboard…";
    try {
      const key = scopeKey();
      const assets = await fetchJSON(`dashboard-assets/${key}`, "Could not load the dashboard.");
      if (version !== loadVersion || board.hidden) return;
      frame = document.createElement("iframe");
      frame.title = heading.textContent;
      // The CSP blocks native submissions. The sandbox permits submit handlers to run.
      frame.setAttribute("sandbox", "allow-scripts allow-forms");
      frame.referrerPolicy = "no-referrer";
      frame.srcdoc = frameDocument(assets);
      preview.replaceChildren(frame);
      message.textContent = "";
    } catch (error) {
      if (version === loadVersion) message.textContent = error.message;
    }
  }

  async function loadSettings(version) {
    try {
      const current = await fetchJSON(`dashboard-settings/${scopeKey()}`, "Could not load dashboard settings.");
      if (version !== loadVersion) return;
      settings = current;
      sendSnapshot();
    } catch (error) {
      if (version === loadVersion) message.textContent = error.message;
    }
  }

  function rememberChange(nodeId, change) {
    settingChanges.set(change.id, { ...change, nodeId });
    if (settingChanges.size > 64) settingChanges.delete(settingChanges.keys().next().value);
  }

  async function changeSetting(data, sender) {
    const { id, nodeId, name, mode, value } = data;
    if (!Number.isSafeInteger(id)) return;
    const runId = mode === "live" && following ? liveRun?.id : null;
    let result;
    try {
      result = await fetchJSON(`dashboard-settings/${scopeKey()}`, "Could not change this setting.", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nodeId, name, mode, value, runId }),
      });
      if (result.status === "queued" && liveRun?.id === runId) {
        if (!settingChanges.has(result.id)) rememberChange(nodeId, result);
        result = settingChanges.get(result.id);
      }
      if (result.status === "saved") await loadSettings(loadVersion);
    } catch (error) {
      result = { status: "rejected", message: error.message };
    }
    if (sender !== frame?.contentWindow) return;
    sender.postMessage({ type: "dashboard-setting-result", id, result }, "*");
    sendSnapshot();
  }

  async function selectRun() {
    const version = ++runVersion;
    const id = runSelect.value;
    following = id === "live";
    savedRun = null;
    sendSnapshot();
    if (!id || following) return;
    try {
      const run = await fetchJSON(`history/${id}`, "Could not load this run.");
      if (version !== runVersion) return;
      savedRun = run;
      sendSnapshot();
    } catch (error) {
      if (version === runVersion) message.textContent = error.message;
    }
  }

  async function listRuns(preferred) {
    const version = ++listVersion;
    const selection = runVersion;
    try {
      const runs = await fetchJSON("history", "Could not load run history.");
      if (version !== listVersion || board.hidden) return;
      if (selection !== runVersion) preferred = runSelect.value;
      runSelect.replaceChildren(new Option("Live run", "live"), ...runs.map(run => new Option(
        `${new Date(run.started_at).toLocaleString()} · ${run.status} · ${run.id.slice(0, 8)}`, run.id,
      )));
      runSelect.value = (preferred === "live" || runs.some(run => run.id === preferred))
        ? preferred : runs[0]?.id ?? "live";
      await selectRun();
    } catch (error) {
      if (version === listVersion) message.textContent = error.message;
    }
  }

  function open(nodeId, preferred = liveRun?.status === "running" ? "live" : null) {
    if (stopped) return;
    scope = nodeId;
    runVersion++;
    savedRun = null;
    following = preferred === "live";
    settings = {};
    heading.textContent = scope === null ? "Workflow dashboard" : `${scope} dashboard`;
    if (board.hidden) opener = document.activeElement;
    board.hidden = false;
    revealBoard();
    loadDashboard();
    loadSettings(loadVersion);
    listRuns(preferred);
  }

  workflowButton.disabled = false;
  workflowButton.addEventListener("click", () => open(null));
  document.querySelector("#close-dashboard").addEventListener("click", () => {
    closeBoard();
    opener?.focus({ preventScroll: true });
  });
  fullscreen.hidden = !document.fullscreenEnabled;
  fullscreen.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement === board) await document.exitFullscreen();
      else await board.requestFullscreen();
    } catch {
      message.textContent = "Could not change dashboard full screen mode.";
    }
  });
  document.addEventListener("fullscreenchange", () => {
    fullscreen.textContent = document.fullscreenElement === board ? "Exit full screen" : "Full screen";
  });
  reload.addEventListener("click", () => {
    loadDashboard();
    loadSettings(loadVersion);
  });
  runSelect.addEventListener("change", selectRun);
  function closeBoard() {
    if (board.hidden) return;
    if (document.fullscreenElement === board) document.exitFullscreen().catch(() => {});
    board.hidden = true;
    loadVersion++;
    listVersion++;
    runVersion++;
    ready = false;
    frame = null;
    preview.replaceChildren();
  }

  window.addEventListener("message", event => {
    if (board.hidden || event.origin !== "null" || event.source !== frame?.contentWindow) return;
    const data = event.data;
    if (data?.type === "dashboard-ready") {
      ready = true;
      sendSnapshot();
    } else if (data?.type === "dashboard-open-node" && scope === null && graph.some(node => node.id === data.nodeId)) {
      open(data.nodeId, runSelect.value);
    } else if (data?.type === "dashboard-set-setting") {
      changeSetting(data, event.source);
    }
  });

  window.addEventListener("workspace-stop", event => {
    stopped = event.detail.phase === "stopped";
    workflowButton.disabled = stopped;
    if (stopped) {
      frames.clear();
      closeBoard();
    }
  });

  function track(event) {
    if (event.result) {
      if (!board.hidden) listRuns(runSelect.value);
      return;
    }
    if (!event.run_id) {
      if (event.error && liveRun?.status === "running") {
        liveRun.status = "failed";
        frames.clear();
        if (following) sendSnapshot();
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
      if (!board.hidden) {
        runVersion++;
        runSelect.value = "live";
        following = true;
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
    if (following) sendSnapshot();
  }

  return { open, track };
}
