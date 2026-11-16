const views = document.querySelector("#sheets");
const tabs = document.querySelector("#sheet-tabs");
const notice = document.querySelector("#notice");
const noticeMessage = document.querySelector("#notice-message");
const reopen = document.querySelector("#open-flow");
const newSheet = document.querySelector("#new-sheet");
const reloadFlow = document.querySelector("#reload-flow");
const reloadMessage = document.querySelector("#reload-message");
const createDialog = document.querySelector("#create-sheet");
const createForm = createDialog.querySelector("form");
const sheetTitle = createForm.querySelector("#sheet-title");
const createError = createForm.querySelector("#create-error");
const createButton = createForm.querySelector('button[type="submit"]');
const cancelSheet = createForm.querySelector("#cancel-sheet");
const deleteDialog = document.querySelector("#delete-sheet");
const deleteForm = deleteDialog.querySelector("form");
const deleteMessage = deleteForm.querySelector("#delete-sheet-message");
const deleteError = deleteForm.querySelector(".dialog-error");
const deleteButton = deleteForm.querySelector('button[type="submit"]');
const cancelDelete = deleteForm.querySelector('button[type="button"]');
const sheets = [];
let active = null;
let sheetToDelete = null;

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
  reloadFlow.disabled = !active || active.pending;
  const reloadError = active?.reloadError ?? "";
  reloadMessage.hidden = !reloadError;
  if (reloadMessage.textContent !== reloadError) reloadMessage.textContent = reloadError;
  if (!active) {
    notice.hidden = false;
    const message = "Create a sheet to get started.";
    if (noticeMessage.textContent !== message) noticeMessage.textContent = message;
    reopen.hidden = true;
    return;
  }
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
reloadFlow.addEventListener("click", () => openFlow(active, "reload"));

async function command(sheet, action) {
  const response = await fetch(`/flows/${sheet.index}/${action}`, { method: "POST" });
  const data = await response.json();
  if (!response.ok) {
    throw Object.assign(new Error(data.error ?? "The action failed."), { status: data.status });
  }
  return data;
}

function showFrame(sheet, revision) {
  const prefix = revision ? `/revision/${revision}` : "";
  const url = `/flows/${sheet.index}${prefix}/view/`;
  if (!sheet.frame || sheet.frame.getAttribute("src") !== url) {
    const frame = document.createElement("iframe");
    frame.title = `${sheet.name} flow`;
    frame.src = url;
    sheet.frame = frame;
    sheet.view.replaceChildren(frame);
  }
}

async function openFlow(sheet, action = "open") {
  if (sheet.pending) return;
  sheet.pending = true;
  sheet.version++;
  sheet.error = null;
  sheet.reloadError = null;
  updateStatus(sheet, "opening");
  showNotice();
  try {
    const { revision } = await command(sheet, action);
    sheet.revision = revision;
    showFrame(sheet, revision);
    updateStatus(sheet, "ready");
  } catch (error) {
    if (error.status) {
      sheet.reloadError = error.message;
      updateStatus(sheet, error.status);
    } else {
      sheet.error = error.message;
      updateStatus(sheet, "unavailable");
    }
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
  sheet.reloadError = null;
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
  for (const item of sheets.filter(Boolean)) {
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
  tab.setAttribute("aria-selected", "false");
  tab.tabIndex = -1;
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
  if (flow.deletable) {
    const remove = element("button", "×", "delete-flow");
    remove.setAttribute("aria-label", `Delete ${flow.name}`);
    remove.title = `Delete ${flow.name}`;
    remove.addEventListener("click", () => confirmDelete(sheet));
    group.append(remove);
  }
  const view = element("section", "", "sheet-view");
  view.id = `flow-view-${index}`;
  view.setAttribute("role", "tabpanel");
  view.setAttribute("aria-labelledby", tab.id);
  view.inert = true;
  views.prepend(view);
  tabs.append(group);

  const sheet = { index, name: flow.name, tab, indicator, stop, group, view, frame: null, pending: false, version: 0, revision: flow.revision };
  updateStatus(sheet, flow.status);
  tab.addEventListener("click", () => selectSheet(sheet));
  stop.addEventListener("click", () => stopFlow(sheet));
  return sheet;
}

tabs.addEventListener("keydown", event => {
  const available = sheets.filter(Boolean);
  const index = available.findIndex(sheet => sheet.tab === event.target);
  if (index < 0) return;
  let next;
  if (event.key === "ArrowLeft") next = (index + available.length - 1) % available.length;
  else if (event.key === "ArrowRight") next = (index + 1) % available.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = available.length - 1;
  else return;
  event.preventDefault();
  selectSheet(available[next]);
  available[next].tab.focus();
});

async function getWorkspace() {
  const response = await fetch("/workspace");
  if (!response.ok) throw new Error("Could not load the Workspace.");
  return response.json();
}

function removeSheet(index) {
  const sheet = sheets[index];
  sheet?.view.remove();
  sheet?.group.remove();
  if (active === sheet) active = null;
  sheets[index] = null;
}

function showCurrentSheet() {
  const first = sheets.find(Boolean);
  if (!active && first) selectSheet(first);
  else showNotice();
}

function syncSheets(flows) {
  flows.forEach((flow, index) => {
    if (flow === null) removeSheet(index);
    else if (sheets[index] === undefined) sheets[index] = makeSheet(flow, index);
  });
  showCurrentSheet();
}

function confirmDelete(sheet) {
  sheetToDelete = sheet;
  deleteError.hidden = true;
  deleteMessage.textContent = `“${sheet.name}” will stop if running. Its Python files and history will move to .workspace/deleted-flows.`;
  deleteDialog.showModal();
}

cancelDelete.addEventListener("click", () => deleteDialog.close());
deleteDialog.addEventListener("cancel", event => {
  if (deleteButton.disabled) event.preventDefault();
});
deleteForm.addEventListener("submit", async event => {
  event.preventDefault();
  deleteError.hidden = true;
  deleteButton.disabled = cancelDelete.disabled = true;
  try {
    await command(sheetToDelete, "delete");
    removeSheet(sheetToDelete.index);
    showCurrentSheet();
    deleteDialog.close();
    (active?.tab ?? newSheet).focus();
  } catch (error) {
    deleteError.textContent = error.message;
    deleteError.hidden = false;
  } finally {
    deleteButton.disabled = cancelDelete.disabled = false;
  }
});

newSheet.addEventListener("click", () => {
  createForm.reset();
  createError.hidden = true;
  createDialog.showModal();
});
cancelSheet.addEventListener("click", () => createDialog.close());
createDialog.addEventListener("cancel", event => {
  if (createButton.disabled) event.preventDefault();
});
createForm.addEventListener("submit", async event => {
  event.preventDefault();
  createError.hidden = true;
  createButton.disabled = cancelSheet.disabled = true;
  try {
    const response = await fetch("/flows", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: sheetTitle.value }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error ?? "Could not create the sheet.");
    syncSheets((await getWorkspace()).flows);
    const sheet = sheets[data.index];
    selectSheet(sheet);
    createDialog.close();
    sheet.tab.focus();
  } catch (error) {
    createError.textContent = error.message;
    createError.hidden = false;
    sheetTitle.focus();
  } finally {
    createButton.disabled = cancelSheet.disabled = false;
  }
});

async function pollStatus() {
  const versions = sheets.map(sheet => sheet?.version);
  try {
    const { flows } = await getWorkspace();
    syncSheets(flows);
    for (const sheet of sheets.filter(Boolean)) {
      if (flows[sheet.index] && !sheet.pending && sheet.version === versions[sheet.index]) {
        const { status, revision } = flows[sheet.index];
        if (revision !== sheet.revision && status !== "unavailable" && status !== "stopped") {
          sheet.revision = revision;
          sheet.error = null;
          sheet.reloadError = null;
          if (sheet.frame) showFrame(sheet, revision);
        }
        if (status === "stopped" && sheet.frame) {
          sheet.view.replaceChildren();
          sheet.frame = null;
        }
        if (status !== "running" && sheet.status === "running") sheet.reloadError = null;
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
  syncSheets(workspace.flows);
  newSheet.disabled = false;
  pollStatus().catch(() => {});
} catch (error) {
  noticeMessage.textContent = error.message;
}
