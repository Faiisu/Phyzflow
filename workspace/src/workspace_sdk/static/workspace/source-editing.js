export function initSourceEditing({
  sheets, workspacePath, getActiveSheet, canEditRunningSource, getDiagnostic,
  showNotice, showFrame, updateStatus,
}) {
  const forceOpenFlow = document.querySelector("#force-open-flow");
  const editFlowSource = document.querySelector("#edit-flow-source");
  const sourceDialog = document.querySelector("#flow-source");
  const sourceForm = sourceDialog.querySelector("form");
  const sourceInput = sourceForm.querySelector("#flow-source-input");
  const sourceMessage = sourceForm.querySelector("#flow-source-message");
  const forceOpenSource = sourceForm.querySelector("#force-open-source");
  const saveSource = sourceForm.querySelector('button[type="submit"]');
  const closeSource = sourceForm.querySelector("#close-flow-source");
  const forceOpenDialog = document.querySelector("#force-open-sheet");
  const forceOpenForm = forceOpenDialog.querySelector("form");
  const forceOpenOwner = forceOpenForm.querySelector("#force-open-owner");
  const forceOpenHeartbeat = forceOpenForm.querySelector("#force-open-heartbeat");
  const forceOpenStatus = forceOpenForm.querySelector("#force-open-status");
  const forceOpenEffect = forceOpenForm.querySelector("#force-open-effect");
  const forceOpenError = forceOpenForm.querySelector("#force-open-error");
  const confirmForceOpen = forceOpenForm.querySelector("#confirm-force-open");
  const cancelForceOpen = forceOpenForm.querySelector("#cancel-force-open");
  let sheetToForceOpen = null;
  let sourceDraft = null;
  let pendingSourceSheet = null;

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
      for (const frame of [sheet.frame, sheet.recoveryFrame]) {
        frame?.contentWindow?.dispatchEvent(new CustomEvent("workspace-edit-lock", {
          detail: { editLock: sheet.editLock ?? null },
        }));
      }
    } catch (error) {
    }
  }

  function findSheetByWindow(frameWindow) {
    return sheets.find(sheet => sheet && (sheet.frame?.contentWindow === frameWindow ||
      sheet.recoveryFrame?.contentWindow === frameWindow)) ?? null;
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
    return { ok: response.ok, data };
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
    if (!sheet) return { ok: false, sessionId: null, error: "This sheet is unavailable.", editLock: null };
    const lock = await acquireSheetEdit(sheet, frameWindow);
    if (lock.ok && findSheetByWindow(frameWindow) !== sheet) void releaseSheetEdit(sheet, frameWindow);
    return lock;
  };

  window.phyzflowReleaseEditLock = frameWindow => {
    const sheet = findSheetByWindow(frameWindow);
    if (sheet) void releaseSheetEdit(sheet, frameWindow);
  };

  window.phyzflowRecoveryNodeBase = frameWindow => {
    const sheet = findSheetByWindow(frameWindow);
    return sheet ? `${workspacePath}flows/${sheet.index}/recovery/` : "";
  };
  window.phyzflowRecoveryDiagnostic = frameWindow =>
    findSheetByWindow(frameWindow) === getActiveSheet() ? getDiagnostic().replaceAll("\\", "/") : "";

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

  function syncFlowSourceLock() {
    if (!sourceDialog.open || !sourceDraft || closeSource.disabled) return;
    const externalLock = externalLockSummary(getActiveSheet()?.editLock);
    const lockMessage = externalLock || (sourceDraft.readOnlyLock ? sourceDraft.lockMessage : "");
    const running = !canEditRunningSource() && sourceDraft.sheet.status === "running";
    const locked = Boolean(lockMessage) || running;
    sourceInput.readOnly = locked;
    saveSource.disabled = locked || !flowSourceIsDirty();
    forceOpenSource.hidden = !externalLock;
    if (lockMessage) {
      sourceMessage.textContent = `Read-only · ${lockMessage}`;
    } else if (running) {
      sourceMessage.textContent = "Flow is running. Editing is disabled.";
    } else if (sourceMessage.textContent === "Flow is running. Editing is disabled.") {
      sourceMessage.textContent = "";
    }
  }

  forceOpenFlow.addEventListener("click", () => openForceOpenDialog(getActiveSheet()));
  forceOpenSource.addEventListener("click", () => openForceOpenDialog(getActiveSheet()));
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
    const editingFlowSource = Boolean(sourceDialog.open && getActiveSheet() === sheet);
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
      } else if (data.worker_ready !== false) {
        sheet.error = null;
        sheet.runError = null;
        sheet.reloadError = null;
        if (data.status !== "running" && data.status !== "stopping") {
          showFrame(sheet, sheet.revision);
        }
        updateStatus(sheet, data.status ?? "ready");
      }
      sheetToForceOpen = null;
      forceOpenDialog.close();
      if (editingFlowSource) {
        try {
          await loadFlowSource(sheet, { ok: true });
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

  async function requestFlowSource(sheet, changes) {
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
    const response = await fetch(`./flows/${sheet.index}/source`, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error ?? "Could not access the source file.");
    return data;
  }

  async function loadFlowSource(sheet, lock) {
    const { source, revision } = await requestFlowSource(sheet, null);
    if (!sourceDialog.open) return;
    sourceDraft = { sheet, source, revision, readOnlyLock: !lock.ok, lockMessage: lock.error ?? "" };
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

  async function openSourceEditor() {
    const sheet = getActiveSheet();
    pendingSourceSheet = sheet;
    sourceDraft = null;
    sourceInput.value = "";
    sourceInput.disabled = saveSource.disabled = closeSource.disabled = true;
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
      await loadFlowSource(sheet, lock);
      if (!sourceDialog.open) {
        if (lock.ok) void releaseSheetEdit(sheet, "flow-source");
        return;
      }
      if (lock.ok) sourceInput.focus();
    } catch (error) {
      if (lock?.ok) void releaseSheetEdit(sheet, "flow-source");
      sourceMessage.textContent = error.message;
    } finally {
      closeSource.disabled = false;
      syncFlowSourceLock();
      if (sourceInput.disabled) closeSource.focus();
    }
  }

  editFlowSource.addEventListener("click", openSourceEditor);

  sourceInput.addEventListener("input", syncFlowSourceLock);

  sourceForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!flowSourceIsDirty() || saveSource.disabled) return;
    const source = sourceInput.value;
    const { sheet, revision } = sourceDraft;
    sourceInput.readOnly = saveSource.disabled = closeSource.disabled = true;
    sourceMessage.textContent = "Saving…";
    try {
      const saved = await requestFlowSource(sheet, { source, revision });
      sourceDraft = { ...sourceDraft, source, revision: saved.revision };
      sourceMessage.textContent = canEditRunningSource()
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


  function isEditingFlowSource(sheet) {
    return sheet === getActiveSheet() && sourceDialog.open && (flowSourceIsDirty() || closeSource.disabled);
  }

  function releaseRemovedSheet(sheet) {
    clearSheetHeartbeat(sheet);
    sheet.activeEditors.clear();
    void releaseSheetEdit(sheet, "remove");
  }

  function syncPolledLock(sheet, nextLock, sentSession) {
    if (JSON.stringify(sheet.editLock) === JSON.stringify(nextLock)) return;
    sheet.editLock = nextLock;
    if (sheet.editSessionId && sheet.editSessionId === sentSession && !nextLock?.owned_by_viewer) {
      clearSheetHeartbeat(sheet);
      sheet.editSessionId = null;
      sheet.activeEditors.clear();
    }
    notifyFrameEditLock(sheet);
    if (sheet === getActiveSheet()) {
      syncFlowSourceLock();
      syncForceOpenDialog();
    }
  }

  return {
    isEditingFlowSource, lockNoticeText, notifyFrameEditLock, releaseRemovedSheet,
    releaseSheetEdit, syncFlowSourceLock, syncPolledLock,
  };
}
