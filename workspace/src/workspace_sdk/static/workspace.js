const workspacePath = window.location.pathname;
document.querySelector("#workspace-home").hidden = workspacePath === "/";
document.querySelector("#workspace-brand").hidden = workspacePath !== "/";
const views = document.querySelector("#sheets");
const tabs = document.querySelector("#sheet-tabs");
const notice = document.querySelector("#notice");
const noticeMessage = document.querySelector("#notice-message");
const recoveryHint = document.querySelector("#recovery-hint");
const reopen = document.querySelector("#open-flow");
const recoverSource = document.querySelector("#recover-flow-source");
const newSheet = document.querySelector("#new-sheet");
const runAll = document.querySelector("#run-all");
const runSheet = document.querySelector("#run-sheet");
const flowStatus = document.querySelector("#workspace-flow-status");
const reloadFlow = document.querySelector("#reload-flow");
const reloadMessage = document.querySelector("#reload-message");
const editLockMessage = document.querySelector("#edit-lock-message");
const forceOpenFlow = document.querySelector("#force-open-flow");
const sourceChangeMessage = document.querySelector("#source-change-message");
const editFlowSource = document.querySelector("#edit-flow-source");
const sourceDialog = document.querySelector("#flow-source");
const sourceForm = sourceDialog.querySelector("form");
const sourceInput = sourceForm.querySelector("#flow-source-input");
const sourceTitle = sourceForm.querySelector("#flow-source-title");
const sourceDiagnostic = sourceForm.querySelector("#flow-source-diagnostic");
const sourceFiles = sourceForm.querySelector("#flow-source-files");
const sourcePath = sourceForm.querySelector("#flow-source-path");
const sourceMessage = sourceForm.querySelector("#flow-source-message");
const forceOpenSource = sourceForm.querySelector("#force-open-source");
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
const forceOpenDialog = document.querySelector("#force-open-sheet");
const forceOpenForm = forceOpenDialog.querySelector("form");
const forceOpenOwner = forceOpenForm.querySelector("#force-open-owner");
const forceOpenHeartbeat = forceOpenForm.querySelector("#force-open-heartbeat");
const forceOpenStatus = forceOpenForm.querySelector("#force-open-status");
const forceOpenEffect = forceOpenForm.querySelector("#force-open-effect");
const forceOpenError = forceOpenForm.querySelector("#force-open-error");
const confirmForceOpen = forceOpenForm.querySelector("#confirm-force-open");
const cancelForceOpen = forceOpenForm.querySelector("#cancel-force-open");
const sheets = [];
let active = null;
let sheetToDelete = null;
let sheetToForceOpen = null;
let sourceDraft = null;
let pendingSourceSheet = null;
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

function formatHeartbeat(timestamp) {
  if (typeof timestamp !== "string" || !Number.isFinite(Date.parse(timestamp))) return "Unavailable";
  return timestamp.replace(/\.\d+/, "").replace("T", " ").replace(/(?:\+00:00|Z)$/, " UTC");
}

function lockStateLabel(lock) {
  if (!lock?.locked) return "";
  if (lock.disconnected) return "Possibly stale (Disconnected)";
  return lock.stale ? "Possibly stale" : "Active";
}

function externalLockSummary(lock) {
  if (!lock?.locked || lock.owned_by_viewer) return "";
  const prefix = lock.stale ? `${lockStateLabel(lock)} · ` : "";
  return `${prefix}Locked for editing by ${lock.owner} · Last heartbeat ${formatHeartbeat(lock.last_heartbeat)}`;
}

function lockNoticeText(lock) {
  const summary = externalLockSummary(lock);
  return summary ? `Read-only · ${summary}` : "";
}

function notifyFrameEditLock(sheet) {
  try {
    sheet.frame?.contentWindow?.dispatchEvent(new CustomEvent("workspace-edit-lock", {
      detail: { editLock: sheet.editLock ?? null },
    }));
  } catch (error) {
  }
}

function findSheetByWindow(frameWindow) {
  return sheets.find(sheet => sheet && sheet.frame?.contentWindow === frameWindow) ?? null;
}

function clearSheetHeartbeat(sheet) {
  if (sheet.heartbeatTimer !== null) {
    clearInterval(sheet.heartbeatTimer);
    sheet.heartbeatTimer = null;
  }
}

async function postEditLock(sheet, body, { keepalive = false } = {}) {
  const response = await fetch(`./flows/${sheet.index}/edit-lock`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    keepalive,
  });
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data };
}

function startSheetHeartbeat(sheet) {
  if (sheet.heartbeatTimer !== null) return;
  const sessionId = sheet.editSessionId;
  sheet.heartbeatTimer = setInterval(() => {
    if (sheet.editSessionId !== sessionId) return;
    postEditLock(sheet, { action: "heartbeat", session_id: sessionId }).then(({ ok: alive, data: status }) => {
      if (sheet.editSessionId !== sessionId) return;
      if (alive) {
        sheet.editLock = status;
        notifyFrameEditLock(sheet);
      } else {
        clearSheetHeartbeat(sheet);
        sheet.editSessionId = null;
        sheet.activeEditors.clear();
        if (status.edit_lock) sheet.editLock = status.edit_lock;
        notifyFrameEditLock(sheet);
        syncFlowSourceLock();
        showNotice();
      }
    }).catch(() => {});
  }, 15000);
}

async function acquireSheetEdit(sheet, editorKey) {
  sheet.version++;
  const payload = sheet.editSessionId
    ? { action: "acquire", session_id: sheet.editSessionId }
    : { action: "acquire" };
  const { ok, data } = await postEditLock(sheet, payload);
  if (!ok) {
    if (data.edit_lock) {
      sheet.editLock = data.edit_lock;
      notifyFrameEditLock(sheet);
      showNotice();
    }
    return {
      ok: false,
      sessionId: null,
      error: data.error ?? "This sheet is locked for editing.",
      editLock: data.edit_lock ?? null,
    };
  }
  sheet.editSessionId = data.session_id;
  sheet.version++;
  sheet.editLock = data;
  sheet.activeEditors.add(editorKey);
  startSheetHeartbeat(sheet);
  notifyFrameEditLock(sheet);
  showNotice();
  return { ok: true, sessionId: sheet.editSessionId, error: "", editLock: data };
}

async function releaseSheetEdit(sheet, editorKey, { keepalive = false } = {}) {
  sheet.activeEditors.delete(editorKey);
  if (sheet.activeEditors.size > 0 || !sheet.editSessionId) return;
  const sessionId = sheet.editSessionId;
  sheet.editSessionId = null;
  clearSheetHeartbeat(sheet);
  sheet.version++;
  try {
    const { ok } = await postEditLock(sheet, { action: "release", session_id: sessionId }, { keepalive });
    if (ok && sheet.editLock?.owned_by_viewer) {
      sheet.editLock = { ...sheet.editLock, locked: false, session_id: null, owner: null, owned_by_viewer: false };
      notifyFrameEditLock(sheet);
      showNotice();
    }
  } catch (error) {
  }
}

window.phyzflowAcquireEditLock = async frameWindow => {
  const sheet = findSheetByWindow(frameWindow);
  if (!sheet) return { ok: true, sessionId: null, error: "", editLock: null };
  return acquireSheetEdit(sheet, "node-files");
};

window.phyzflowReleaseEditLock = frameWindow => {
  const sheet = findSheetByWindow(frameWindow);
  if (sheet) void releaseSheetEdit(sheet, "node-files");
};

window.phyzflowEditSessionId = frameWindow => findSheetByWindow(frameWindow)?.editSessionId ?? null;
window.phyzflowSheetEditLock = frameWindow => findSheetByWindow(frameWindow)?.editLock ?? null;
window.phyzflowRequestForceOpen = frameWindow => {
  const sheet = findSheetByWindow(frameWindow);
  if (sheet) openForceOpenDialog(sheet);
};

function renderForceOpenDetails(lock) {
  forceOpenOwner.textContent = lock?.owner ?? "Unavailable";
  forceOpenHeartbeat.textContent = formatHeartbeat(lock?.last_heartbeat);
  forceOpenStatus.textContent = lockStateLabel(lock) || "Unavailable";
  forceOpenEffect.textContent = lock?.has_unsaved_draft
    ? "This session has unsaved edits. Force opening will end the stuck editing session, discard any unsaved edits from that session, and load the latest saved source from disk."
    : "Force opening will end the stuck editing session, discard any unsaved edits from that session, and load the latest saved source from disk.";
}

function syncForceOpenDialog() {
  if (!forceOpenDialog.open || !sheetToForceOpen || confirmForceOpen.disabled) return;
  const lock = sheetToForceOpen.editLock;
  if (!lock?.locked || lock.owned_by_viewer) {
    forceOpenDialog.close();
    return;
  }
  renderForceOpenDetails(lock);
}

function openForceOpenDialog(sheet) {
  const lock = sheet?.editLock;
  if (!lock?.locked || lock.owned_by_viewer) return;
  sheetToForceOpen = sheet;
  forceOpenError.hidden = true;
  renderForceOpenDetails(lock);
  forceOpenDialog.showModal();
}

function flowSourceLocked() {
  if (sourceDraft?.readOnlyLock) return true;
  if (active?.editLock?.locked && !active.editLock.owned_by_viewer) return true;
  return !editRunningSource && sourceDraft?.sheet.status === "running";
}

function syncFlowSourceLock() {
  if (!sourceDialog.open || !sourceDraft || closeSource.disabled) return;
  const externalLock = active?.editLock?.locked && !active.editLock.owned_by_viewer
    ? externalLockSummary(active.editLock)
    : sourceDraft.readOnlyLock
      ? sourceDraft.lockMessage
      : "";
  const running = !editRunningSource && sourceDraft?.sheet.status === "running";
  const locked = Boolean(externalLock) || running;
  sourceInput.readOnly = locked;
  saveSource.disabled = locked || !flowSourceIsDirty();
  forceOpenSource.hidden = !Boolean(active?.editLock?.locked && !active.editLock.owned_by_viewer);
  if (externalLock) {
    sourceMessage.textContent = `Read-only · ${externalLock}`;
  } else if (running) {
    sourceMessage.textContent = "Flow is running. Editing is disabled.";
  } else if (sourceMessage.textContent === "Flow is running. Editing is disabled.") {
    sourceMessage.textContent = "";
  }
}

function needsRecovery(sheet) {
  return sheet && (sheet.status === "unavailable" || Boolean(sheet.runError));
}

function usesSourceGraph(sheet) {
  return editRunningSource && sheet?.workerReady === false && sheet.status !== "stopped";
}

function updateStatus(sheet, status) {
  sheet.status = status;
  sheet.view.classList.toggle("is-unavailable", needsRecovery(sheet) || usesSourceGraph(sheet));
  sheet.indicator.textContent = labels[status] ?? status;
  sheet.indicator.dataset.state = status;
  sheet.stop.disabled = sheet.pending || !["running", "stopping"].includes(status);
  if (sheet === active) syncFlowSourceLock();
}

function activeRunButton() {
  if (!active || active.pending || active.frameStopped || needsRecovery(active) || usesSourceGraph(active) ||
      ["opening", "running", "stopping", "stopped", "unavailable"].includes(active.status)) return null;
  return active.frame?.contentDocument?.getElementById("run-flow");
}

function canRunSourceGraph(sheet) {
  return usesSourceGraph(sheet) && !sheet.pending && !["opening", "running", "stopping"].includes(sheet.status);
}

function syncToolbar() {
  const button = activeRunButton();
  runSheet.disabled = !canRunSourceGraph(active) && button?.disabled !== false;
  const frameStatus = button?.ownerDocument.getElementById("status");
  const frameState = frameStatus?.dataset.state;
  const state = frameState || active?.status || "";
  const label = frameState ? frameStatus.textContent : labels[state] ?? state;
  flowStatus.hidden = !active;
  if (flowStatus.textContent !== label) flowStatus.textContent = label;
  flowStatus.dataset.state = state;
}

window.phyzflowSyncToolbar = frameWindow => {
  if (active?.frame?.contentWindow === frameWindow) syncToolbar();
};

runSheet.addEventListener("click", async () => {
  const sheet = active;
  if (!canRunSourceGraph(sheet)) {
    activeRunButton()?.click();
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
    showNotice();
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
  const recovering = Boolean((sourceGraph || needsRecovery(active)) && !active.pending);
  const hasRecoveryGraph = sourceGraph || (recovering && active.recoverable);
  recoverSource.hidden = !recovering;
  recoveryHint.hidden = !hasRecoveryGraph;
  if (hasRecoveryGraph) {
    const hint = active.recoverable
      ? "Last loaded graph. Select a node to edit its source."
      : "Source preview. Select a node to edit its source.";
    if (recoveryHint.textContent !== hint) recoveryHint.textContent = hint;
  }
  notice.classList.toggle("is-recovery", hasRecoveryGraph);
  showRecoveryGraph(active, hasRecoveryGraph);
  reloadFlow.disabled = !active || active.pending || active.status === "running";
  editFlowSource.disabled = !active || active.pending;
  const deferSource = editRunningSource;
  const pendingSource = deferSource ? "Source changed. Run to use the saved code." : "Source changed.";
  const change = active?.sourceChange && active?.status !== "running" ? pendingSource : "";
  const reloadText = active?.reloadError || change;
  reloadMessage.hidden = !reloadText;
  if (reloadMessage.textContent !== reloadText) reloadMessage.textContent = reloadText;
  const lockText = lockNoticeText(active?.editLock);
  editLockMessage.hidden = !lockText;
  if (editLockMessage.textContent !== lockText) editLockMessage.textContent = lockText;
  forceOpenFlow.hidden = !lockText || Boolean(active?.pending);
  sourceChangeMessage.hidden = !change;
  const sourceChangeText = deferSource ? pendingSource : "Source changed. Reload pending.";
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
  const message = error ?? (sourceGraph ? "" : {
    opening: "Opening the flow…",
    stopping: "Stopping the flow…",
    stopped: snapshot ? "Stopped · Run snapshot" : "Flow stopped.",
    unavailable: "The flow process is unavailable.",
  }[active.status] ?? "Open this flow to get started.");
  noticeMessage.hidden = !message;
  if (noticeMessage.textContent !== message) noticeMessage.textContent = message;
  reopen.hidden = active.pending || sourceGraph;
}

reopen.addEventListener("click", () => openFlow(active));
reloadFlow.addEventListener("click", () => openFlow(active, "reload"));
forceOpenFlow.addEventListener("click", () => openForceOpenDialog(active));
forceOpenSource.addEventListener("click", () => openForceOpenDialog(active));
cancelForceOpen.addEventListener("click", () => forceOpenDialog.close());
forceOpenDialog.addEventListener("cancel", event => {
  if (confirmForceOpen.disabled) event.preventDefault();
});
forceOpenDialog.addEventListener("close", () => {
  if (!confirmForceOpen.disabled) sheetToForceOpen = null;
});
forceOpenForm.addEventListener("submit", async event => {
  event.preventDefault();
  const sheet = sheetToForceOpen;
  if (!sheet || sheet.pending) return;
  forceOpenError.hidden = true;
  confirmForceOpen.disabled = cancelForceOpen.disabled = true;
  sheet.pending = true;
  sheet.version++;
  const editingFlowSource = Boolean(sourceDialog.open && active === sheet);
  try {
    const payload = { action: "force_open", acquire: editingFlowSource };
    if (sheet.editSessionId) payload.session_id = sheet.editSessionId;
    const { ok, data } = await postEditLock(sheet, payload);
    if (!ok) {
      if (data.edit_lock) {
        sheet.editLock = data.edit_lock;
        notifyFrameEditLock(sheet);
        syncFlowSourceLock();
      }
      throw new Error(data.error ?? "Could not force open this sheet.");
    }
    sheet.version++;
    sheet.editLock = data.edit_lock ?? null;
    sheet.sourceChange = null;
    if (data.edit_lock?.owned_by_viewer && data.edit_lock.session_id) {
      sheet.editSessionId = data.edit_lock.session_id;
      sheet.activeEditors.add("flow-source");
      startSheetHeartbeat(sheet);
    } else {
      clearSheetHeartbeat(sheet);
      sheet.editSessionId = null;
      sheet.activeEditors.clear();
    }
    if (data.revision !== undefined) sheet.revision = data.revision;
    if (data.worker_ready !== undefined) sheet.workerReady = data.worker_ready;
    if (data.reload_error) {
      sheet.reloadError = data.reload_error;
      updateStatus(sheet, data.status ?? "unavailable");
    } else {
      sheet.error = null;
      sheet.runError = null;
      sheet.reloadError = null;
      if (data.worker_ready !== false && data.status !== "running" && data.status !== "stopping") {
        showFrame(sheet, sheet.revision);
      }
      updateStatus(sheet, data.status ?? "ready");
    }
    sheetToForceOpen = null;
    forceOpenDialog.close();
    if (editingFlowSource) {
      try {
        await loadFlowSource(sheet, sourceDraft?.path ?? "flow.py", { ok: true });
        sourceMessage.textContent = "Force opened. Loaded latest saved source.";
        syncFlowSourceLock();
        sourceInput.focus();
      } catch (sourceError) {
        sourceMessage.textContent = sourceError.message;
      }
    }
    notifyFrameEditLock(sheet);
  } catch (error) {
    forceOpenError.textContent = error.message;
    forceOpenError.hidden = false;
  } finally {
    sheet.pending = false;
    sheet.version++;
    confirmForceOpen.disabled = cancelForceOpen.disabled = false;
    showNotice();
  }
});

async function requestFlowSource(sheet, changes, query = "") {
  const headers = {};
  if (sheet.editSessionId) headers["X-Phyzflow-Edit-Session"] = sheet.editSessionId;
  const options = changes
    ? {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify(changes),
      }
    : Object.keys(headers).length
      ? { headers }
      : undefined;
  const response = await fetch(`./flows/${sheet.index}/source${query}`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error ?? "Could not access the source file.");
  return data;
}

function sourceQuery(path) {
  return path === "flow.py" ? "" : `?path=${encodeURIComponent(path)}`;
}

async function loadFlowSource(sheet, path, lock) {
  const { source, revision } = await requestFlowSource(sheet, null, sourceQuery(path));
  if (!sourceDialog.open) return;
  sourceDraft = { sheet, path, source, revision, readOnlyLock: !lock.ok, lockMessage: lock.error ?? "" };
  sourceInput.value = source;
  sourceInput.disabled = false;
  sourceMessage.textContent = lock.ok ? "" : `Read-only · ${lock.error}`;
}

function flowSourceIsDirty() {
  return sourceDialog.open && sourceDraft && sourceInput.value !== sourceDraft.source;
}

function mayCloseSource() {
  return !closeSource.disabled && (!flowSourceIsDirty() || window.confirm("Discard unsaved source changes?"));
}

function recoverySourcePath(files, preferred) {
  const path = preferred?.replaceAll("\\", "/");
  if (files.includes(path)) {
    const parts = path.split("/");
    const folder = parts[0] === "nodes" && parts.length >= 3
      ? parts.slice(0, 2).join("/")
      : parts.slice(0, -1).join("/");
    const diagnostic = noticeMessage.textContent.replaceAll("\\", "/");
    return files.find(file => folder && file.startsWith(`${folder}/`) && diagnostic.includes(`/${file}:`)) ?? path;
  }
  return files.includes("flow.py") ? "flow.py" : files[0];
}

async function openSourceEditor(recovering, preferred) {
  const sheet = active;
  pendingSourceSheet = sheet;
  sourceDraft = null;
  sourceInput.value = "";
  sourceInput.disabled = sourcePath.disabled = saveSource.disabled = closeSource.disabled = true;
  sourceFiles.hidden = !recovering;
  sourceTitle.textContent = recovering ? "Edit source" : "Edit flow.py";
  sourceDiagnostic.hidden = !recovering;
  sourceDiagnostic.textContent = recovering ? noticeMessage.textContent : "";
  forceOpenSource.hidden = true;
  sourceMessage.textContent = "Loading source…";
  sourceDialog.showModal();
  let lock = null;
  try {
    lock = await acquireSheetEdit(sheet, "flow-source");
    if (!sourceDialog.open) {
      if (lock.ok) void releaseSheetEdit(sheet, "flow-source");
      return;
    }
    let path = "flow.py";
    if (recovering) {
      const { files } = await requestFlowSource(sheet, null, "?files");
      if (!files.length) throw new Error("No editable Python files were found.");
      sourcePath.replaceChildren(...files.map(filename => new Option(filename, filename)));
      path = recoverySourcePath(files, preferred);
      sourcePath.value = path;
      sheet.watching = true;
    }
    await loadFlowSource(sheet, path, lock);
    if (!sourceDialog.open) {
      if (lock.ok) void releaseSheetEdit(sheet, "flow-source");
      return;
    }
    if (recovering) sourcePath.disabled = false;
    if (recovering) sourcePath.focus();
    else if (lock.ok) sourceInput.focus();
  } catch (error) {
    if (lock?.ok) void releaseSheetEdit(sheet, "flow-source");
    sourceMessage.textContent = error.message;
  } finally {
    closeSource.disabled = false;
    syncFlowSourceLock();
    if (sourceInput.disabled) closeSource.focus();
  }
}

editFlowSource.addEventListener("click", () => openSourceEditor(false));
recoverSource.addEventListener("click", () => openSourceEditor(true));
window.phyzflowEditRecoveryNode = (frameWindow, path) => {
  if (active?.recoveryFrame?.contentWindow === frameWindow && (needsRecovery(active) || usesSourceGraph(active)) &&
      !sourceDialog.open) void openSourceEditor(true, path);
};

sourcePath.addEventListener("change", async () => {
  const draft = sourceDraft;
  if (!draft || !mayCloseSource()) {
    if (draft) sourcePath.value = draft.path;
    return;
  }
  const path = sourcePath.value;
  sourcePath.disabled = sourceInput.disabled = closeSource.disabled = saveSource.disabled = true;
  sourceMessage.textContent = "Loading source…";
  try {
    await loadFlowSource(draft.sheet, path, { ok: !draft.readOnlyLock, error: draft.lockMessage });
  } catch (error) {
    sourcePath.value = draft.path;
    sourceMessage.textContent = error.message;
  } finally {
    sourcePath.disabled = sourceInput.disabled = closeSource.disabled = false;
    syncFlowSourceLock();
  }
});

sourceInput.addEventListener("input", () => {
  saveSource.disabled = flowSourceLocked() || !flowSourceIsDirty();
});

sourceForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!flowSourceIsDirty() || saveSource.disabled) return;
  const source = sourceInput.value;
  const { sheet, path, revision } = sourceDraft;
  sourceInput.readOnly = sourcePath.disabled = saveSource.disabled = closeSource.disabled = true;
  sourceMessage.textContent = "Saving…";
  try {
    const saved = await requestFlowSource(sheet, { source, revision }, sourceQuery(path));
    sourceDraft = { ...sourceDraft, sheet, source, revision: saved.revision };
    sourceMessage.textContent = editRunningSource
      ? "Saved. The next run will use this source."
      : "Saved. Python changes reload automatically.";
  } catch (error) {
    sourceMessage.textContent = error.message;
  } finally {
    sourcePath.disabled = closeSource.disabled = false;
    syncFlowSourceLock();
  }
});

closeSource.addEventListener("click", () => {
  if (mayCloseSource()) sourceDialog.close();
});
sourceDialog.addEventListener("close", () => {
  const sheet = sourceDraft?.sheet ?? pendingSourceSheet;
  pendingSourceSheet = null;
  if (sheet) void releaseSheetEdit(sheet, "flow-source");
});
sourceDialog.addEventListener("cancel", event => {
  if (!mayCloseSource()) event.preventDefault();
});
window.addEventListener("pagehide", () => {
  for (const sheet of sheets.filter(Boolean)) {
    if (sheet.editSessionId) {
      sheet.activeEditors.clear();
      void releaseSheetEdit(sheet, "pagehide", { keepalive: true });
    }
  }
});
window.addEventListener("beforeunload", event => {
  if (flowSourceIsDirty()) {
    event.preventDefault();
    event.returnValue = true;
  }
});

function hasUnsavedSource(sheet) {
  try {
    return Boolean(sheet.frame?.contentDocument?.documentElement.hasAttribute("data-unsaved-source"));
  } catch (error) {
    return false;
  }
}

function mayReplaceView(sheet) {
  return !hasUnsavedSource(sheet) || window.confirm("Discard unsaved source changes in this sheet?");
}

async function command(sheet, action) {
  const options = { method: "POST" };
  if (action === "delete" && sheet.editSessionId) {
    options.headers = { "X-Phyzflow-Edit-Session": sheet.editSessionId };
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
    const frame = document.createElement("iframe");
    frame.title = `${sheet.name} flow`;
    frame.src = url;
    frame.addEventListener("load", () => {
      notifyFrameEditLock(sheet);
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
    const { revision, status, editor } = await command(sheet, automaticSource ? `reload/${automaticSource}` : action);
    sheet.revision = revision;
    sheet.sourceChange = null;
    sheet.workerReady = !editor;
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
    showNotice();
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
  sheet.tab.scrollIntoView({ block: "nearest", inline: "nearest" });
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

  const sheet = {
    index, name: flow.name, tab, indicator, stop, group, view,
    frame: null, frameStopped: false, recoveryFrame: null, recoverable: flow.recoverable, pending: false, version: 0,
    workerReady: flow.worker_ready,
    revision: flow.revision, runError: flow.error,
    editLock: flow.edit_lock ?? null, editSessionId: null, activeEditors: new Set(), heartbeatTimer: null,
  };
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
  const sessionIds = sheets.filter(Boolean).map(sheet => sheet.editSessionId).filter(Boolean);
  const options = sessionIds.length
    ? { headers: { "X-Phyzflow-Edit-Session": sessionIds.join(",") } }
    : undefined;
  const response = await fetch("./workspace", options);
  if (!response.ok) throw new Error("Could not load the Workspace.");
  return response.json();
}

function removeSheet(index) {
  const sheet = sheets[index];
  if (sheet) {
    clearSheetHeartbeat(sheet);
    sheet.activeEditors.clear();
    void releaseSheetEdit(sheet, "remove");
  }
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
  const sentSessions = sheets.map(sheet => sheet?.editSessionId ?? null);
  try {
    const { flows } = await getWorkspace();
    syncSheets(flows);
    for (const sheet of sheets.filter(Boolean)) {
      if (flows[sheet.index] && sheet.version === versions[sheet.index]) {
        const nextLock = flows[sheet.index].edit_lock ?? null;
        if (JSON.stringify(sheet.editLock) !== JSON.stringify(nextLock)) {
          sheet.editLock = nextLock;
          if (sheet.editSessionId && sheet.editSessionId === sentSessions[sheet.index] && !nextLock?.owned_by_viewer) {
            clearSheetHeartbeat(sheet);
            sheet.editSessionId = null;
            sheet.activeEditors.clear();
          }
          notifyFrameEditLock(sheet);
          if (sheet === active) {
            syncFlowSourceLock();
            syncForceOpenDialog();
          }
        }
      }
      if (flows[sheet.index] && (!sheet.frameStopped || !runAll.hidden) && !sheet.pending && sheet.version === versions[sheet.index]) {
        const { status, revision, changed_source, error, recoverable, worker_ready } = flows[sheet.index];
        sheet.recoverable = recoverable;
        sheet.workerReady = worker_ready;
        if ((revision !== sheet.revision || (sheet === active && !sheet.frame && status === "running" && revision > 0) ||
            (sheet === active && worker_ready && sheet.recoveryFrame)) &&
            !["opening", "unavailable", "stopped"].includes(status) && !error && !hasUnsavedSource(sheet)) {
          sheet.revision = revision;
          sheet.error = null;
          sheet.reloadError = null;
          if (sheet.frame || (sheet === active && (status === "running" || worker_ready))) showFrame(sheet, revision);
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
  runAll.hidden = !workspace.run_all;
  editRunningSource = workspace.edit_running_source;
  syncSheets(workspace.flows);
  newSheet.disabled = false;
  pollStatus().catch(() => {});
} catch (error) {
  noticeMessage.textContent = error.message;
}
