const dialog = document.querySelector("#workspace-dashboard");
const select = document.querySelector("#workspace-dashboard-panel");
const status = document.querySelector("#workspace-dashboard-status");
const content = document.querySelector("#workspace-dashboard-content");
const resizeHandle = document.querySelector("#resize-workspace-dashboard");
let session;
let drag;

function resizeDashboard(width, height) {
  dialog.style.width = `${Math.max(0, width)}px`;
  dialog.style.height = `${Math.max(0, height)}px`;
}

resizeHandle.addEventListener("pointerdown", event => {
  if (!event.isPrimary || event.button !== 0) return;
  const { width, height } = dialog.getBoundingClientRect();
  drag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, width, height };
  resizeHandle.setPointerCapture(event.pointerId);
  dialog.classList.add("is-resizing");
  event.preventDefault();
});
resizeHandle.addEventListener("pointermove", event => {
  if (drag?.pointerId === event.pointerId) {
    resizeDashboard(drag.width + event.clientX - drag.x, drag.height + event.clientY - drag.y);
  }
});
resizeHandle.addEventListener("lostpointercapture", () => {
  drag = null;
  dialog.classList.remove("is-resizing");
});
resizeHandle.addEventListener("keydown", event => {
  const delta = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] }[event.key];
  if (!delta) return;
  event.preventDefault();
  const { width, height } = dialog.getBoundingClientRect();
  resizeDashboard(width + delta[0], height + delta[1]);
});

function showStatus(text) {
  status.textContent = text;
  status.hidden = !text;
}

async function readDashboard(path, signal) {
  const response = await fetch(path, { signal });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error ?? "Could not load the Workspace dashboard.");
  }
  return response.json();
}

function startSession() {
  session?.abort();
  const controller = new AbortController();
  session = controller;
  content.replaceChildren();
  showStatus("Loading dashboard…");
  return controller.signal;
}

async function loadPanel() {
  const signal = startSession();
  const path = `./workspace-dashboard/${encodeURIComponent(select.value)}`;
  try {
    const [assets, { createDashboardFrame }] = await Promise.all([
      readDashboard(path, signal),
      import("../dashboard-ui.js"),
    ]);
    if (signal.aborted) return;
    const frame = createDashboardFrame(assets, select.selectedOptions[0].textContent);
    let timer;
    let previous;
    async function poll() {
      try {
        const snapshot = await readDashboard(`${path}/data`, signal);
        if (signal.aborted) return;
        showStatus("");
        const serialized = JSON.stringify(snapshot);
        if (serialized !== previous) {
          frame.contentWindow.postMessage({ type: "dashboard-data", snapshot }, "*");
          previous = serialized;
        }
      } catch (error) {
        if (!signal.aborted) showStatus(error.message);
      } finally {
        if (!signal.aborted) timer = setTimeout(poll, 500);
      }
    }
    function ready(event) {
      if (event.source !== frame.contentWindow || event.data?.type !== "dashboard-ready") return;
      window.removeEventListener("message", ready);
      poll();
    }
    signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
    window.addEventListener("message", ready, { signal });
    content.replaceChildren(frame);
  } catch (error) {
    if (!signal.aborted) showStatus(error.message);
  }
}

document.querySelector("#open-workspace-dashboard").addEventListener("click", async () => {
  dialog.showModal();
  select.hidden = true;
  const signal = startSession();
  try {
    const { panels } = await readDashboard("./workspace-dashboard", signal);
    if (signal.aborted) return;
    select.replaceChildren(...panels.map(panel => new Option(panel.label, panel.id)));
    select.hidden = panels.length < 2;
    if (panels.length) loadPanel();
    else showStatus("Add a panel in dashboards/workspace to display data from this Workspace.");
  } catch (error) {
    if (!signal.aborted) showStatus(error.message);
  }
});

select.addEventListener("change", loadPanel);
dialog.addEventListener("close", () => {
  session?.abort();
  content.replaceChildren();
});
