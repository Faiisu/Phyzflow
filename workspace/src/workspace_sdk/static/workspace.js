const workspacePath = window.location.pathname;
document.querySelector("#workspace-home").hidden = workspacePath === "/";
const views = document.querySelector("#sheets");
const tabs = document.querySelector("#sheet-tabs");
const notice = document.querySelector("#notice");
const noticeMessage = document.querySelector("#notice-message");
const reopen = document.querySelector("#open-flow");
const newSheet = document.querySelector("#new-sheet");
const runAll = document.querySelector("#run-all");
const reloadFlow = document.querySelector("#reload-flow");
const reloadMessage = document.querySelector("#reload-message");
const sourceChangeMessage = document.querySelector("#source-change-message");
const editFlowSource = document.querySelector("#edit-flow-source");
const sourceDialog = document.querySelector("#flow-source");
const sourceForm = sourceDialog.querySelector("form");
const sourceInput = sourceForm.querySelector("#flow-source-input");
const sourceMessage = sourceForm.querySelector("#flow-source-message");
const saveSource = sourceForm.querySelector('button[type="submit"]');
const closeSource = sourceForm.querySelector("#close-flow-source");
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
let sourceDraft = null;
let editRunningSource = false;

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

function flowSourceLocked() {
  return !editRunningSource && sourceDraft?.sheet.status === "running";
}

function syncFlowSourceLock() {
  if (!sourceDialog.open || !sourceDraft || closeSource.disabled) return;
  const running = flowSourceLocked();
  sourceInput.readOnly = running;
  saveSource.disabled = running || !flowSourceIsDirty();
  if (running) {
    sourceMessage.textContent = "Flow is running. Editing is disabled.";
  } else if (sourceMessage.textContent === "Flow is running. Editing is disabled.") {
    sourceMessage.textContent = "";
  }
}

function updateStatus(sheet, status) {
  sheet.status = status;
  sheet.indicator.textContent = labels[status] ?? status;
  sheet.indicator.dataset.state = status;
  sheet.stop.disabled = sheet.pending || !["running", "stopping"].includes(status);
  if (sheet === active) syncFlowSourceLock();
}

function showNotice() {
  reloadFlow.disabled = !active || active.pending || active.status === "running";
  editFlowSource.disabled = !active || active.pending;
  const change = active?.sourceChange && active?.status !== "running" ? "Source changed." : "";
  const reloadText = active?.reloadError || change;
  reloadMessage.hidden = !reloadText;
  if (reloadMessage.textContent !== reloadText) reloadMessage.textContent = reloadText;
  sourceChangeMessage.hidden = !change;
  const snapshot = Boolean(active?.frameStopped);
  notice.classList.toggle("is-snapshot", snapshot);
  if (!active) {
    notice.hidden = false;
    const message = "Create a sheet to get started.";
    if (noticeMessage.textContent !== message) noticeMessage.textContent = message;
    reopen.hidden = true;
    return;
  }
  const error = active.runError ?? active.error;
  notice.hidden = Boolean(active.frame) && !snapshot && active.status !== "unavailable" && !error;
  if (notice.hidden) return;
  const message = error ?? {
    opening: "Opening the flow…",
    stopping: "Stopping the flow…",
    stopped: snapshot ? "Stopped · Run snapshot" : "Flow stopped.",
    unavailable: "The flow process is unavailable.",
  }[active.status] ?? "Open this flow to get started.";
  if (noticeMessage.textContent !== message) noticeMessage.textContent = message;
  reopen.hidden = active.pending;
}

reopen.addEventListener("click", () => openFlow(active));
reloadFlow.addEventListener("click", () => openFlow(active, "reload"));

async function requestFlowSource(sheet, changes) {
  const response = await fetch(`./flows/${sheet.index}/source`, changes && {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error ?? "Could not access flow.py.");
  return data;
}

function flowSourceIsDirty() {
  return sourceDialog.open && sourceDraft && sourceInput.value !== sourceDraft.source;
}

function mayCloseSource() {
  return !closeSource.disabled && (!flowSourceIsDirty() || window.confirm("Discard unsaved changes to flow.py?"));
}

editFlowSource.addEventListener("click", async () => {
  const sheet = active;
  sourceDraft = null;
  sourceInput.value = "";
  sourceInput.disabled = saveSource.disabled = closeSource.disabled = true;
  sourceMessage.textContent = "Loading source…";
  sourceDialog.showModal();
  try {
    const { source, revision } = await requestFlowSource(sheet);
    sourceDraft = { sheet, source, revision };
    sourceInput.value = source;
    sourceInput.disabled = false;
    sourceMessage.textContent = "";
    sourceInput.focus();
  } catch (error) {
    sourceMessage.textContent = error.message;
  } finally {
    closeSource.disabled = false;
    syncFlowSourceLock();
    if (sourceInput.disabled) closeSource.focus();
  }
});

sourceInput.addEventListener("input", () => {
  saveSource.disabled = flowSourceLocked() || !flowSourceIsDirty();
});

sourceForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!flowSourceIsDirty() || saveSource.disabled) return;
  const source = sourceInput.value;
  const { sheet, revision } = sourceDraft;
  sourceInput.readOnly = saveSource.disabled = closeSource.disabled = true;
  sourceMessage.textContent = "Saving…";
  try {
    const saved = await requestFlowSource(sheet, { source, revision });
    sourceDraft = { sheet, source, revision: saved.revision };
    sourceMessage.textContent = editRunningSource
      ? "Saved. The next run will use this source."
      : "Saved. Python changes reload automatically.";
  } catch (error) {
    sourceMessage.textContent = error.message;
  } finally {
    closeSource.disabled = false;
    syncFlowSourceLock();
  }
});

closeSource.addEventListener("click", () => {
  if (mayCloseSource()) sourceDialog.close();
});
sourceDialog.addEventListener("cancel", event => {
  if (!mayCloseSource()) event.preventDefault();
});
window.addEventListener("beforeunload", event => {
  if (flowSourceIsDirty()) {
    event.preventDefault();
    event.returnValue = true;
  }
});

function hasUnsavedSource(sheet) {
  return sheet.frame?.contentDocument?.documentElement.hasAttribute("data-unsaved-source");
}

function mayReplaceView(sheet) {
  return !hasUnsavedSource(sheet) || window.confirm("Discard unsaved source changes in this sheet?");
}

async function command(sheet, action) {
  const response = await fetch(`./flows/${sheet.index}/${action}`, { method: "POST" });
  const data = await response.json();
  if (!response.ok) {
    throw Object.assign(new Error(data.error ?? "The action failed."), { status: data.status, deferred: data.deferred });
  }
  return data;
}

function showFrame(sheet, revision) {
  const prefix = revision ? `/revision/${revision}` : "";
  const url = `${workspacePath}flows/${sheet.index}${prefix}/view/`;
  if (!sheet.frame || sheet.frameStopped || sheet.frame.getAttribute("src") !== url) {
    const frame = document.createElement("iframe");
    frame.title = `${sheet.name} flow`;
    frame.src = url;
    sheet.frame = frame;
    sheet.frameStopped = false;
    sheet.view.replaceChildren(frame);
  }
}

async function openFlow(sheet, action = "open", automaticSource = null) {
  if (sheet.pending || ((action === "reload" || sheet.frameStopped) && !mayReplaceView(sheet))) return;
  const previous = { status: sheet.status, error: sheet.error, reloadError: sheet.reloadError };
  const frozen = automaticSource && sheet.frame?.contentDocument?.documentElement;
  if (frozen) frozen.inert = true;
  sheet.watching = true;
  sheet.pending = true;
  sheet.version++;
  sheet.error = null;
  sheet.runError = null;
  sheet.reloadError = null;
  updateStatus(sheet, "opening");
  showNotice();
  try {
    const { revision } = await command(sheet, automaticSource ? `reload/${automaticSource}` : action);
    sheet.revision = revision;
    sheet.sourceChange = null;
    showFrame(sheet, revision);
    updateStatus(sheet, "ready");
  } catch (error) {
    if (error.deferred || (automaticSource && error.status)) {
      sheet.error = previous.error;
      sheet.reloadError = previous.reloadError;
      updateStatus(sheet, error.status ?? previous.status);
    } else if (error.status) {
      sheet.reloadError = error.message;
      updateStatus(sheet, error.status);
    } else {
      sheet.error = error.message;
      updateStatus(sheet, "unavailable");
    }
  } finally {
    if (frozen) frozen.inert = false;
    sheet.pending = false;
    sheet.version++;
    showNotice();
  }
}

function checkSourceChange(sheet, source) {
  if (sheet.sourceChange !== source) {
    sheet.sourceChange = source;
    sheet.sourceChangedAt = performance.now();
  }
  if (!source || performance.now() - sheet.sourceChangedAt < 500 ||
      (editRunningSource && ["succeeded", "failed"].includes(sheet.status)) ||
      ["running", "opening"].includes(sheet.status) || hasUnsavedSource(sheet) ||
      sheet.frame?.contentDocument?.querySelector("#node-files")?.open ||
      (sheet === active && sourceDialog.open && (flowSourceIsDirty() || closeSource.disabled))) return;
  void openFlow(sheet, "reload", source);
}

function notifyFrameOfStop(sheet, phase, discardUnsaved = false) {
  // Both frames are same-origin. Notify synchronously before closing the run stream.
  sheet.frame?.contentWindow?.dispatchEvent(new CustomEvent("workspace-stop", {
    detail: { phase, discardUnsaved },
  }));
}

function retainStoppedFrame(sheet, discardUnsaved) {
  sheet.frameStopped = Boolean(sheet.frame);
  notifyFrameOfStop(sheet, "stopped", discardUnsaved);
}

async function stopFlow(sheet) {
  if (sheet.pending || !mayReplaceView(sheet)) return;
  sheet.pending = true;
  sheet.version++;
  sheet.reloadError = null;
  updateStatus(sheet, "stopping");
  notifyFrameOfStop(sheet, "stopping");
  try {
    await command(sheet, "stop");
    sheet.sourceChange = null;
    retainStoppedFrame(sheet, true);
    updateStatus(sheet, "stopped");
  } catch (error) {
    notifyFrameOfStop(sheet, null);
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
  if (!sheet.frame && sheet.status !== "stopped" && !sheet.error && !sheet.runError) {
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

  const sheet = { index, name: flow.name, tab, indicator, stop, group, view, frame: null, frameStopped: false, pending: false, version: 0, revision: flow.revision, runError: flow.error };
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

runAll.addEventListener("click", async () => {
  runAll.disabled = true;
  try {
    const response = await fetch("./run-all", { method: "POST" });
    if (!response.ok) throw new Error("Could not start the Workspace sheets.");
    const { errors } = await response.json();
    if (active && errors[active.index]) {
      active.error = errors[active.index];
      showNotice();
    }
  } catch (error) {
    if (active) {
      active.error = error.message;
      showNotice();
    }
  } finally {
    runAll.disabled = false;
  }
});

async function getWorkspace() {
  const response = await fetch("./workspace");
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
    const response = await fetch("./flows", {
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
      if (flows[sheet.index] && (!sheet.frameStopped || !runAll.hidden) && !sheet.pending && sheet.version === versions[sheet.index]) {
        const { status, revision, changed_source, error } = flows[sheet.index];
        if ((revision !== sheet.revision || (sheet === active && !sheet.frame && status === "running" && revision > 0)) && !["opening", "unavailable", "stopped"].includes(status) && !hasUnsavedSource(sheet)) {
          sheet.revision = revision;
          sheet.error = null;
          sheet.reloadError = null;
          if (sheet.frame || (sheet === active && status === "running")) showFrame(sheet, revision);
        }
        if (status === "stopped") retainStoppedFrame(sheet, false);
        sheet.runError = error;
        if (status !== "running" && sheet.status === "running") sheet.reloadError = null;
        updateStatus(sheet, status);
        if (sheet.watching) checkSourceChange(sheet, changed_source);
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
  runAll.hidden = !workspace.run_all;
  editRunningSource = workspace.edit_running_source;
  newSheet.disabled = false;
  pollStatus().catch(() => {});
} catch (error) {
  noticeMessage.textContent = error.message;
}
