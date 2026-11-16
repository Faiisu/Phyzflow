const views = document.querySelector("#sheets");
const tabs = document.querySelector("#sheet-tabs");
const notice = document.querySelector("#notice");
const noticeMessage = document.querySelector("#notice-message");
const reopen = document.querySelector("#open-flow");
const sheets = [];
let active = null;

const labels = {
  ready: "Ready", opening: "Opening…", running: "Running", succeeded: "Succeeded",
  failed: "Failed", stopping: "Stopping…", stopped: "Stopped", unavailable: "Unavailable",
};

function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function updateStatus(sheet, status) {
  sheet.status = status;
  sheet.indicator.textContent = labels[status] ?? status;
  sheet.indicator.dataset.state = status;
  sheet.stop.disabled = sheet.pending || status !== "running";
}

function showNotice() {
  if (!active) return;
  notice.hidden = Boolean(active.frame) && active.status !== "unavailable" && !active.error;
  if (notice.hidden) return;
  const message = active.error ?? {
    opening: "Opening the flow…",
    stopping: "Stopping the flow…",
    stopped: "Flow stopped.",
    unavailable: "The flow process is unavailable.",
  }[active.status] ?? "Open this flow to get started.";
  if (noticeMessage.textContent !== message) noticeMessage.textContent = message;
  reopen.hidden = active.pending;
}

reopen.addEventListener("click", () => openFlow(active));

async function command(sheet, action) {
  const response = await fetch(`/flows/${sheet.index}/${action}`, { method: "POST" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error ?? "The action failed.");
  return data;
}

async function openFlow(sheet) {
  if (sheet.pending) return;
  sheet.pending = true;
  sheet.version++;
  sheet.error = null;
  updateStatus(sheet, "opening");
  showNotice();
  try {
    const { url } = await command(sheet, "open");
    if (!sheet.frame || sheet.frame.getAttribute("src") !== url) {
      const frame = document.createElement("iframe");
      frame.title = `${sheet.name} flow`;
      frame.src = url;
      sheet.frame = frame;
      sheet.view.replaceChildren(frame);
    }
    updateStatus(sheet, "ready");
  } catch (error) {
    sheet.error = error.message;
    updateStatus(sheet, "unavailable");
  } finally {
    sheet.pending = false;
    sheet.version++;
    showNotice();
  }
}

async function stopFlow(sheet) {
  if (sheet.pending) return;
  sheet.pending = true;
  sheet.version++;
  updateStatus(sheet, "stopping");
  try {
    await command(sheet, "stop");
    sheet.view.replaceChildren();
    sheet.frame = null;
    updateStatus(sheet, "stopped");
  } catch (error) {
    sheet.error = error.message;
    updateStatus(sheet, "unavailable");
  } finally {
    sheet.pending = false;
    sheet.version++;
    showNotice();
  }
}

function selectSheet(sheet) {
  active = sheet;
  for (const item of sheets) {
    const selected = item === active;
    item.tab.setAttribute("aria-selected", String(selected));
    item.tab.tabIndex = selected ? 0 : -1;
    item.view.classList.toggle("is-active", selected);
    item.view.inert = !selected;
  }
  if (!sheet.frame && sheet.status !== "stopped" && !sheet.error) {
    openFlow(sheet);
  }
  showNotice();
}

function makeSheet(flow, index) {
  const tab = element("button", "", "sheet-tab");
  tab.id = `flow-tab-${index}`;
  tab.setAttribute("role", "tab");
  tab.setAttribute("aria-controls", `flow-view-${index}`);
  tab.setAttribute("aria-label", flow.name);
  const title = element("span", flow.name, "sheet-name");
  title.title = flow.name;
  const indicator = element("span", "", "sheet-status");
  tab.setAttribute("aria-describedby", `flow-status-${index}`);
  indicator.id = `flow-status-${index}`;
  tab.append(title, indicator);
  const stop = element("button", "Stop", "stop-flow");
  stop.setAttribute("aria-label", `Stop ${flow.name}`);
  const group = element("div", "", "sheet");
  group.setAttribute("role", "presentation");
  group.append(tab, stop);
  const view = element("section", "", "sheet-view");
  view.id = `flow-view-${index}`;
  view.setAttribute("role", "tabpanel");
  view.setAttribute("aria-labelledby", tab.id);
  views.prepend(view);
  tabs.append(group);

  const sheet = { index, name: flow.name, tab, indicator, stop, view, frame: null, pending: false, version: 0 };
  updateStatus(sheet, flow.status);
  tab.addEventListener("click", () => selectSheet(sheet));
  stop.addEventListener("click", () => stopFlow(sheet));
  return sheet;
}

tabs.addEventListener("keydown", event => {
  const index = sheets.findIndex(sheet => sheet.tab === event.target);
  if (index < 0) return;
  let next;
  if (event.key === "ArrowLeft") next = (index + sheets.length - 1) % sheets.length;
  else if (event.key === "ArrowRight") next = (index + 1) % sheets.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = sheets.length - 1;
  else return;
  event.preventDefault();
  selectSheet(sheets[next]);
  sheets[next].tab.focus();
});

async function getWorkspace() {
  const response = await fetch("/workspace");
  if (!response.ok) throw new Error("Could not load the Workspace.");
  return response.json();
}

async function pollStatus() {
  const versions = sheets.map(sheet => sheet.version);
  try {
    const { flows } = await getWorkspace();
    for (const sheet of sheets) {
      if (!sheet.pending && sheet.version === versions[sheet.index]) {
        const status = flows[sheet.index].status;
        if (status === "stopped" && sheet.frame) {
          sheet.view.replaceChildren();
          sheet.frame = null;
        }
        updateStatus(sheet, status);
      }
    }
    showNotice();
  } finally {
    setTimeout(() => pollStatus().catch(() => {}), 250);
  }
}

try {
  const workspace = await getWorkspace();
  const name = document.querySelector("#workspace-name");
  name.textContent = workspace.name;
  name.title = workspace.name;
  sheets.push(...workspace.flows.map(makeSheet));
  selectSheet(sheets[0]);
  pollStatus().catch(() => {});
} catch (error) {
  noticeMessage.textContent = error.message;
}
