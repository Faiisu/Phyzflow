import { createLiveSync, fetchConditionalJSON } from "./live-sync.js";
import { initSourceEditing } from "./workspace/source-editing.js";
import { initSheetDialogs } from "./workspace/sheet-dialogs.js";

const workspacePath = window.location.pathname;
if (workspacePath !== "/") {
  const home = document.querySelector("#workspace-home");
  home.href = "../../";
  home.title = "Workspaces";
  home.setAttribute("aria-label", home.title);
  home.removeAttribute("aria-hidden");
}
const views = document.querySelector("#sheets");
const tabs = document.querySelector("#sheet-tabs");
const notice = document.querySelector("#notice");
const noticeMessage = document.querySelector("#notice-message");
const recoveryHint = document.querySelector("#recovery-hint");
const reopen = document.querySelector("#open-flow");
const newSheet = document.querySelector("#new-sheet");
const runAll = document.querySelector("#run-all");
const renameSheet = document.querySelector("#rename-sheet");
const runSheet = document.querySelector("#run-sheet");
const flowStatus = document.querySelector("#workspace-flow-status");
const reloadFlow = document.querySelector("#reload-flow");
const reloadMessage = document.querySelector("#reload-message");
const editLockMessage = document.querySelector("#edit-lock-message");
const forceOpenFlow = document.querySelector("#force-open-flow");
const sourceChangeMessage = document.querySelector("#source-change-message");
const editFlowSource = document.querySelector("#edit-flow-source");
const sheets = [];
let active = null;
let editRunningSource = false;
let dashboardModalOpen = false;
let workspaceSync = null;

function sheetActivityDetail(sheet) {
  return {
    active: Boolean(sheet && sheet === active && !dashboardModalOpen && !sheet.frameStopped),
    running: sheet?.status === "running",
    runId: sheet?.runId ?? null,
  };
}

function notifySheetActivity(sheet) {
  if (!sheet?.frame?.contentWindow) return;
  try {
    sheet.frame.contentWindow.dispatchEvent(new CustomEvent("workspace-sheet-activity", {
      detail: sheetActivityDetail(sheet),
    }));
  } catch {
  }
}

function syncAllSheetActivity() {
  for (const sheet of sheets) {
    if (sheet) notifySheetActivity(sheet);
  }
}

window.phyzflowSheetState = frameWindow => {
  const sheet = sheets.find(item => item?.frame?.contentWindow === frameWindow);
  return sheet ? sheetActivityDetail(sheet) : { active: true, running: false, runId: null };
};

window.addEventListener("workspace-dashboard-modal", event => {
  dashboardModalOpen = Boolean(event.detail?.open);
  workspaceSync?.setActive(!dashboardModalOpen);
  syncAllSheetActivity();
});

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

const {
  isEditingFlowSource, lockNoticeText, notifyFrameEditLock, releaseRemovedSheet,
  releaseSheetEdit, syncFlowSourceLock, syncPolledLock,
} = initSourceEditing({
  sheets, workspacePath, getActiveSheet: () => active,
  canEditRunningSource: () => editRunningSource, getDiagnostic: () => noticeMessage.textContent,
  showNotice, showFrame, updateStatus,
});
const { confirmDelete } = initSheetDialogs({
  sheets, getActiveSheet: () => active, command, getWorkspace, removeSheet,
  selectSheet, showCurrentSheet, syncSheets, updateSheetName,
});

function needsRecovery(sheet) {
  return sheet && (sheet.status === "unavailable" || Boolean(sheet.runError));
}

function usesSourceGraph(sheet) {
  if (sheet?.status === "stopped") return !sheet.frame;
  return editRunningSource && sheet?.workerReady === false;
}

function updateStatus(sheet, status) {
  const runningChanged = (sheet.status === "running") !== (status === "running");
  sheet.status = status;
  sheet.view.classList.toggle("is-unavailable", needsRecovery(sheet) || usesSourceGraph(sheet));
  sheet.indicator.textContent = labels[status] ?? status;
  sheet.indicator.dataset.state = status;
  sheet.stop.hidden = !["running", "stopping"].includes(status);
  sheet.stop.disabled = sheet.pending || sheet.stop.hidden;
  if (sheet === active) syncFlowSourceLock();
  if (runningChanged) notifySheetActivity(sheet);
}

function activeRunControls() {
  if (!active || active.pending || active.frameStopped || needsRecovery(active) || usesSourceGraph(active) ||
      ["opening", "running", "stopping", "stopped", "unavailable"].includes(active.status)) return null;
  return active.frame?.contentWindow?.phyzflowRunControls;
}

function canRunWithoutWorker(sheet) {
  return editRunningSource && (usesSourceGraph(sheet) || sheet?.status === "stopped") &&
    !sheet.pending && !["opening", "running", "stopping"].includes(sheet.status);
}

function syncToolbar() {
  renameSheet.disabled = !active;
  const run = activeRunControls()?.snapshot;
  runSheet.disabled = !canRunWithoutWorker(active) && !run?.canRun;
  const state = run?.status?.state || active?.status || "";
  const label = run?.status?.label ?? labels[state] ?? state;
  flowStatus.hidden = !active;
  if (flowStatus.textContent !== label) flowStatus.textContent = label;
  flowStatus.dataset.state = state;
}

window.phyzflowRunChanged = frameWindow => {
  if (active?.frame?.contentWindow !== frameWindow) return;
  syncToolbar();
  const state = activeRunControls()?.snapshot?.status?.state ?? null;
  if (state !== active.lastFrameRunState) {
    active.lastFrameRunState = state;
    workspaceSync?.wake();
  }
};

runSheet.addEventListener("click", async () => {
  const sheet = active;
  if (!canRunWithoutWorker(sheet)) {
    activeRunControls()?.run();
    return;
  }
  sheet.pending = true;
  sheet.version++;
  sheet.error = sheet.runError = null;
  updateStatus(sheet, "running");
  showNotice();
  try {
    await command(sheet, "run");
  } catch (error) {
    sheet.error = error.message;
    updateStatus(sheet, error.status ?? "failed");
  } finally {
    sheet.pending = false;
    sheet.version++;
    updateStatus(sheet, sheet.status);
    showNotice();
    workspaceSync?.resetEtag();
    workspaceSync?.wake();
  }
});

function showRecoveryGraph(sheet, visible) {
  if (!sheet) return;
  if (visible && !sheet.recoveryFrame) {
    const frame = element("iframe", "", "sheet-recovery");
    frame.title = `${sheet.name} recovery graph`;
    frame.src = `./flows/${sheet.index}/recovery/`;
    sheet.recoveryFrame = frame;
    sheet.view.append(frame);
  }
  if (sheet.recoveryFrame) sheet.recoveryFrame.hidden = !visible;
}

function showNotice() {
  syncToolbar();
  const sourceGraph = usesSourceGraph(active);
  const stopped = active?.status === "stopped";
  const recovering = Boolean((sourceGraph || needsRecovery(active)) && !active.pending);
  const hasRecoveryGraph = sourceGraph || recovering;
  const hasRecoveryError = Boolean(
    active && (needsRecovery(active) || active.error || (active.status === "failed" && hasRecoveryGraph)),
  );
  const showRecoveryHint = hasRecoveryGraph && hasRecoveryError;
  recoveryHint.hidden = !showRecoveryHint;
  if (showRecoveryHint) {
    const hint = active.recoverable
      ? "Last loaded graph. Inspect a node to view its files."
      : "Source preview. Inspect a node to view its files.";
    if (recoveryHint.textContent !== hint) recoveryHint.textContent = hint;
  }
  notice.classList.toggle("is-recovery", hasRecoveryGraph);
  showRecoveryGraph(active, hasRecoveryGraph);
  reloadFlow.disabled = !active || active.pending || active.status === "running";
  editFlowSource.disabled = !active || active.pending;
  const pendingSource = editRunningSource ? "Source changed. Run to use the saved code." : "Source changed.";
  const change = active?.sourceChange && active.status !== "running" ? pendingSource : "";
  const reloadText = active?.reloadError || change;
  reloadMessage.hidden = !reloadText;
  if (reloadMessage.textContent !== reloadText) reloadMessage.textContent = reloadText;
  const lockText = lockNoticeText(active?.editLock);
  editLockMessage.hidden = !lockText;
  if (editLockMessage.textContent !== lockText) editLockMessage.textContent = lockText;
  forceOpenFlow.hidden = !lockText || Boolean(active?.pending);
  sourceChangeMessage.hidden = !change;
  const sourceChangeText = editRunningSource ? pendingSource : "Source changed. Reload pending.";
  if (sourceChangeMessage.textContent !== sourceChangeText) sourceChangeMessage.textContent = sourceChangeText;
  const snapshot = Boolean(active?.frameStopped) && !hasRecoveryGraph;
  notice.classList.toggle("is-snapshot", snapshot);
  if (!active) {
    notice.hidden = false;
    noticeMessage.hidden = false;
    const message = "Create a sheet to get started.";
    if (noticeMessage.textContent !== message) noticeMessage.textContent = message;
    reopen.hidden = true;
    return;
  }
  const error = active.runError ?? active.error;
  notice.hidden = !hasRecoveryGraph && Boolean(active.frame) && !snapshot && active.status !== "unavailable" && !error;
  if (notice.hidden) return;
  const message = error ?? (sourceGraph && !stopped ? "" : {
    opening: "Opening the flow…",
    stopping: "Stopping the flow…",
    stopped: snapshot ? "Stopped · Run snapshot" : "Flow stopped.",
    unavailable: "The flow process is unavailable.",
  }[active.status] ?? "Open this flow to get started.");
  noticeMessage.hidden = !message;
  if (noticeMessage.textContent !== message) noticeMessage.textContent = message;
  reopen.hidden = active.pending || (editRunningSource && (sourceGraph || stopped));
  notice.hidden = !message && !showRecoveryHint && reopen.hidden;
}

reopen.addEventListener("click", () => openFlow(active));
reloadFlow.addEventListener("click", () => openFlow(active, "reload"));

function hasUnsavedSource(sheet) {
  try {
    return [sheet.frame, sheet.recoveryFrame].some(
      frame => frame?.contentDocument?.documentElement.hasAttribute("data-unsaved-source"),
    );
  } catch (error) {
    return false;
  }
}

function mayReplaceView(sheet) {
  return !hasUnsavedSource(sheet) || window.confirm("Discard unsaved source changes in this sheet?");
}

async function command(sheet, action, body = null) {
  const options = { method: "POST" };
  if (body) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  if ((action === "delete" || action === "rename") && sheet.editSessionId) {
    options.headers = { ...options.headers, "X-Phyzflow-Edit-Session": sheet.editSessionId };
  }
  const response = await fetch(`./flows/${sheet.index}/${action}`, options);
  const data = await response.json();
  if (!response.ok) {
    throw Object.assign(new Error(data.error ?? "The action failed."), { status: data.status, deferred: data.deferred });
  }
  return data;
}

function showFrame(sheet, revision) {
  const prefix = revision ? `/revision/${revision}` : "";
  const url = `${workspacePath}flows/${sheet.index}${prefix}/view/`;
  if (!sheet.frame || sheet.frameStopped || sheet.recoveryFrame || sheet.frame.getAttribute("src") !== url) {
    for (const previous of [sheet.frame, sheet.recoveryFrame]) {
      if (sheet.activeEditors.has(previous?.contentWindow)) void releaseSheetEdit(sheet, previous.contentWindow);
    }
    const frame = document.createElement("iframe");
    frame.title = `${sheet.name} flow`;
    frame.src = url;
    frame.addEventListener("load", () => {
      notifyFrameEditLock(sheet);
      notifySheetActivity(sheet);
      if (sheet === active) syncToolbar();
    });
    sheet.frame = frame;
    sheet.recoveryFrame = null;
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
    const { revision, status, editor, startup } = await command(sheet, automaticSource ? `reload/${automaticSource}` : action);
    sheet.revision = revision;
    sheet.sourceChange = null;
    sheet.workerReady = !editor;
    sheet.error = startup?.error ?? null;
    if (!editor) showFrame(sheet, revision);
    updateStatus(sheet, status ?? "ready");
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
    updateStatus(sheet, sheet.status);
    showNotice();
    workspaceSync?.resetEtag();
    workspaceSync?.wake();
  }
}

function checkSourceChange(sheet, source) {
  if (sheet.sourceChange !== source) {
    sheet.sourceChange = source;
    sheet.sourceChangedAt = performance.now();
  }
  if (!source || performance.now() - sheet.sourceChangedAt < 500 ||
      editRunningSource ||
      ["running", "opening"].includes(sheet.status) || hasUnsavedSource(sheet) ||
      sheet.frame?.contentDocument?.querySelector("#node-files")?.open ||
      isEditingFlowSource(sheet)) return;
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
  showNotice();
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
    updateStatus(sheet, sheet.status);
    showNotice();
    workspaceSync?.resetEtag();
    workspaceSync?.wake();
  }
}

function selectSheet(sheet) {
  active = sheet;
  for (const item of sheets.filter(Boolean)) {
    const selected = item === active;
    item.tab.setAttribute("aria-selected", String(selected));
    item.tab.setAttribute("aria-describedby", selected ? flowStatus.id : item.indicator.id);
    item.tab.tabIndex = selected ? 0 : -1;
    item.view.classList.toggle("is-active", selected);
    item.view.inert = !selected;
  }
  syncAllSheetActivity();
  sheet.tab.scrollIntoView({ block: "nearest", inline: "nearest" });
  if (!sheet.frame && sheet.status !== "stopped" && !sheet.error && !sheet.runError) {
    openFlow(sheet);
  }
  showNotice();
}

function updateSheetName(sheet, name) {
  sheet.name = name;
  sheet.tab.setAttribute("aria-label", name);
  sheet.title.textContent = sheet.title.title = name;
  sheet.stop.setAttribute("aria-label", `Stop ${name}`);
  if (sheet.remove) {
    sheet.remove.title = `Delete ${name}`;
    sheet.remove.setAttribute("aria-label", sheet.remove.title);
  }
  if (sheet.frame) sheet.frame.title = `${name} flow`;
  if (sheet.recoveryFrame) sheet.recoveryFrame.title = `${name} recovery graph`;
}

function makeSheet(flow, index) {
  const tab = element("button", "", "sheet-tab");
  tab.id = `flow-tab-${index}`;
  tab.setAttribute("role", "tab");
  tab.setAttribute("aria-controls", `flow-view-${index}`);
  tab.setAttribute("aria-selected", "false");
  tab.tabIndex = -1;
  const title = element("span", "", "sheet-name");
  const indicator = element("span", "", "sheet-status");
  tab.setAttribute("aria-describedby", `flow-status-${index}`);
  indicator.id = `flow-status-${index}`;
  tab.append(title, indicator);
  const stop = element("button", "Stop", "stop-flow");
  const group = element("div", "", "sheet");
  group.setAttribute("role", "presentation");
  group.append(tab, stop);
  const remove = flow.deletable ? element("button", "×", "delete-flow") : null;
  if (remove) {
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

  const sheet = {
    index, tab, title, indicator, stop, remove, group, view,
    frame: null, frameStopped: false, recoveryFrame: null, recoverable: flow.recoverable, pending: false, version: 0,
    workerReady: flow.worker_ready,
    revision: flow.revision, runId: flow.run_id ?? null, runError: flow.error, lastFrameRunState: null,
    editLock: flow.edit_lock ?? null, editSessionId: null, activeEditors: new Set(), heartbeatTimer: null,
  };
  updateSheetName(sheet, flow.name);
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
    workspaceSync?.resetEtag();
    workspaceSync?.wake();
  }
});

function workspaceSessionHeaders() {
  const sessionIds = sheets.filter(Boolean).map(sheet => sheet.editSessionId).filter(Boolean);
  return sessionIds.length ? { "X-Phyzflow-Edit-Session": sessionIds.join(",") } : {};
}

async function fetchWorkspaceState({ signal, etag } = {}) {
  return fetchConditionalJSON("./workspace", {
    signal,
    etag,
    headers: workspaceSessionHeaders(),
    errorMessage: "Could not load the Workspace.",
  });
}

async function getWorkspace() {
  const { data } = await fetchWorkspaceState();
  return data;
}

function removeSheet(index) {
  const sheet = sheets[index];
  if (sheet) {
    releaseRemovedSheet(sheet);
    sheet.view.remove();
    sheet.group.remove();
  }
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
    const sheet = sheets[index];
    if (flow === null) removeSheet(index);
    else if (sheet === undefined) sheets[index] = makeSheet(flow, index);
    else if (sheet && sheet.name !== flow.name) updateSheetName(sheet, flow.name);
  });
  showCurrentSheet();
}

function showFormatNotice(notice) {
  const formatNotice = document.querySelector("#format-notice");
  formatNotice.textContent = notice ?? "";
  formatNotice.hidden = !notice;
}

function workspaceNextDelayMs() {
  for (const sheet of sheets) {
    if (!sheet) continue;
    if (sheet.pending || ["opening", "running", "stopping"].includes(sheet.status)) return 400;
    if (sheet.watching && sheet.sourceChange && !editRunningSource) return 400;
  }
  return 1000;
}

function checkPendingSourceReloads() {
  for (const sheet of sheets) {
    if (sheet?.watching && sheet.sourceChange) {
      checkSourceChange(sheet, sheet.sourceChange);
    }
  }
}

function startWorkspaceSync(initialEtag) {
  let pendingVersions = [];
  let pendingSessions = [];
  workspaceSync = createLiveSync({
    initialEtag,
    fetchUpdate: async ({ signal, etag }) => {
      pendingVersions = sheets.map(sheet => sheet?.version);
      pendingSessions = sheets.map(sheet => sheet?.editSessionId ?? null);
      const result = await fetchWorkspaceState({ signal, etag });
      return {
        status: result.status,
        etag: result.etag,
        data: result.data,
        nextDelayMs: workspaceNextDelayMs,
      };
    },
    onUnchanged: () => {
      checkPendingSourceReloads();
    },
    onUpdate: ({ flows, format_notice }) => {
      showFormatNotice(format_notice);
      syncSheets(flows);
      for (const sheet of sheets.filter(Boolean)) {
        const { index } = sheet;
        const flow = flows[index];
        if (!flow || sheet.version !== pendingVersions[index]) continue;
        syncPolledLock(sheet, flow.edit_lock ?? null, pendingSessions[index]);
        if (sheet.pending || (sheet.frameStopped && runAll.hidden)) continue;
        const { status, revision, changed_source, error, recoverable, worker_ready } = flow;
        const nextRunId = flow.run_id ?? null;
        const runIdChanged = nextRunId !== sheet.runId;
        sheet.runId = nextRunId;
        sheet.recoverable = recoverable;
        sheet.workerReady = worker_ready;
        if ((revision !== sheet.revision || (sheet === active && !sheet.frame && status === "running" && revision > 0) ||
            (sheet === active && worker_ready && sheet.recoveryFrame)) &&
            !["opening", "unavailable", "stopped"].includes(status) && worker_ready !== false &&
            !error && !hasUnsavedSource(sheet)) {
          sheet.revision = revision;
          sheet.error = null;
          sheet.reloadError = null;
          if (sheet.frame || (sheet === active && (status === "running" || worker_ready))) showFrame(sheet, revision);
        }
        if (status === "stopped") retainStoppedFrame(sheet, false);
        sheet.runError = error;
        if (status !== "running" && sheet.status === "running") sheet.reloadError = null;
        updateStatus(sheet, status);
        if (runIdChanged) notifySheetActivity(sheet);
        if (sheet.watching) checkSourceChange(sheet, changed_source);
      }
      showNotice();
    },
  });
  workspaceSync.start({ immediate: false, delayMs: workspaceNextDelayMs() });
}

try {
  const initial = await fetchWorkspaceState();
  const workspace = initial.data;
  const name = document.querySelector("#workspace-name");
  name.textContent = workspace.name;
  name.title = workspace.name;
  showFormatNotice(workspace.format_notice);
  runAll.hidden = !workspace.run_all;
  editRunningSource = workspace.edit_running_source;
  syncSheets(workspace.flows);
  newSheet.disabled = false;
  startWorkspaceSync(initial.etag);
} catch (error) {
  noticeMessage.textContent = error.message;
}
