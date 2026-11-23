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

function childBridge(version, subscribeOnly, fileAccess) {
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
  if (fileAccess) bridge.readFile = (path) => {
    const id = ++request;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      parent.postMessage({ type: "dashboard-read-file", id, path }, "*");
    });
  };
  if (!subscribeOnly) Object.assign(bridge, {
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
    } else if (event.data?.type === "dashboard-file-result") {
      const job = pending.get(event.data.id);
      if (!job?.reject) return;
      pending.delete(event.data.id);
      if (event.data.file instanceof ArrayBuffer) job.resolve(event.data.file);
      else job.reject(new Error("Dashboard file unavailable."));
    } else if (event.data?.type === "dashboard-setting-result") {
      pending.get(event.data.id)?.(event.data.result);
      pending.delete(event.data.id);
    }
  });
}

function bridgeManifest(assets) {
  if (!Object.hasOwn(assets, "bridge.json")) return { versions: [1] };
  try {
    return JSON.parse(assets["bridge.json"]);
  } catch {
    throw new Error("Invalid bridge.json. Declare Dashboard Bridge versions as a non-empty list of positive integers.");
  }
}

function supportedBridgeVersions(assets) {
  const versions = bridgeManifest(assets)?.versions;
  if (!Array.isArray(versions) || !versions.length || !versions.every(version => Number.isSafeInteger(version) && version > 0)) {
    throw new Error("Invalid bridge.json. Declare Dashboard Bridge versions as a non-empty list of positive integers.");
  }
  return versions;
}

function frameDocument(assets, subscribeOnly, fileAccess) {
  const themeCss = JSON.stringify(SHARED_DASHBOARD_THEME_CSS).replaceAll("<", "\\u003c");
  const css = JSON.stringify(assets["style.css"] ?? "").replaceAll("<", "\\u003c");
  const scripts = JSON.stringify(
    [assets["setting.js"], assets["dashboard.js"]].filter(Boolean),
  ).replaceAll("<", "\\u003c");
  return `<!doctype html>
    <meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; ${fileAccess ? "media-src blob:; " : ""}base-uri 'none'; form-action 'none'">
    <script>
    (${childBridge})(${assets.bridge_version}, ${subscribeOnly}, ${fileAccess});
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

export function createDashboardFrame(assets, title, subscribeOnly = false, fileAccess = false) {
  const supported = supportedBridgeVersions(assets);
  if (!supported.includes(assets.bridge_version)) {
    throw new Error(`Incompatible Dashboard Bridge. Observed version ${assets.bridge_version}. Supported versions ${supported.join(", ")}.`);
  }
  const frame = document.createElement("iframe");
  frame.title = title;
  frame.setAttribute("sandbox", "allow-scripts allow-forms");
  frame.referrerPolicy = "no-referrer";
  frame.srcdoc = frameDocument(assets, subscribeOnly, fileAccess);
  return frame;
}

export async function fetchJSON(path, errorMessage, options) {
  const response = await fetch(path, options);
  const isJSON = response.headers.get("Content-Type")?.startsWith("application/json");
  const data = isJSON ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(data?.error ?? errorMessage);
    error.status = response.status;
    throw error;
  }
  return data;
}

export function mountDashboardFrame(preview, assets, title, { subscribeOnly = false, onReady, onMessage, fileURL } = {}) {
  const fileAccess = Boolean(fileURL && typeof bridgeManifest(assets)?.file_root === "string");
  const frame = createDashboardFrame(assets, title, subscribeOnly, fileAccess);
  const controller = new AbortController();
  const { signal } = controller;
  let ready = false;
  let pendingFile;
  const session = {
    signal,
    send(message) {
      const settingResult = !subscribeOnly && message.type === "dashboard-setting-result";
      if (!signal.aborted && (ready || settingResult)) frame.contentWindow?.postMessage(message, "*");
    },
    close() {
      controller.abort();
      frame.remove();
    },
  };
  window.addEventListener("message", event => {
    if (event.origin !== "null" || event.source !== frame.contentWindow) return;
    if (event.data?.type === "dashboard-ready") {
      if (ready) return;
      ready = true;
      onReady?.(session);
    } else if (event.data?.type === "dashboard-read-file") {
      const { id, path } = event.data;
      if (!fileAccess || !Number.isSafeInteger(id) || typeof path !== "string" || path.length > 1024) return;
      pendingFile?.abort();
      const download = new AbortController();
      pendingFile = download;
      const url = new URL(fileURL, document.baseURI);
      url.searchParams.set("path", path);
      void fetch(url, { signal: AbortSignal.any([signal, download.signal]), cache: "no-store" })
        .then((response) => {
          if (!response.ok || response.headers.get("Content-Type") !== "application/octet-stream") throw new Error();
          return response.arrayBuffer();
        })
        .then((file) => {
          if (!signal.aborted) frame.contentWindow?.postMessage({ type: "dashboard-file-result", id, file }, "*", [file]);
        }, () => {
          if (!signal.aborted) frame.contentWindow?.postMessage({ type: "dashboard-file-result", id }, "*");
        });
    } else if (!subscribeOnly) {
      onMessage?.(event.data, session);
    }
  }, { signal });
  preview.replaceChildren(frame);
  return session;
}
