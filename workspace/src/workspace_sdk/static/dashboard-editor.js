export function initDashboardEditor({ scopes, request, acquireEdit, releaseEdit, onCreate, onSave, onRemove }) {
  const dialog = document.querySelector("#dashboard-editor");
  const form = dialog.querySelector("form");
  const field = name => dialog.querySelector(`[data-editor="${name}"]`);
  const heading = dialog.querySelector("h2");
  const scope = dialog.querySelector("#dashboard-editor-scope");
  const id = dialog.querySelector("#dashboard-editor-id");
  const file = dialog.querySelector("#dashboard-editor-file");
  const source = dialog.querySelector("#dashboard-editor-source");
  const status = field("status");
  const submit = form.querySelector('[type="submit"]');
  const add = document.querySelector("#add-dashboard");
  let mode, target, saved, revision, selectedFile;
  let busy = false;
  let editSessionId = null;
  let editLockError = "";
  let pendingRelease;
  let leaving = false;

  scopes.forEach(({ title }, index) => scope.add(new Option(title, String(index))));

  function dirty() {
    return mode === "edit" && revision !== null && source.value !== saved;
  }

  function canDiscard() {
    return !dirty() || window.confirm("Discard unsaved dashboard changes?");
  }

  function sync() {
    file.disabled = busy;
    source.disabled = busy || revision === null;
    source.readOnly = Boolean(editLockError);
    id.disabled = busy;
    scope.disabled = busy || scopes.length === 1;
    submit.disabled = busy || Boolean(editLockError) || (mode === "edit" && !dirty());
    field("close").disabled = busy;
  }

  function release(keepalive = false) {
    if (!editSessionId) return;
    editSessionId = null;
    pendingRelease = releaseEdit({ keepalive });
  }

  function close() {
    dialog.close();
    release();
  }

  async function loadFile() {
    selectedFile = file.value;
    revision = null;
    saved = source.value = "";
    status.textContent = "Loading source…";
    busy = true;
    sync();
    try {
      const result = await request(target.nodeId, { action: "read", panel: target.panelId, name: selectedFile });
      revision = result.revision;
      saved = source.value = result.source;
      status.textContent = editLockError || (revision === "missing" ? "Save to create this file." : "");
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false;
      sync();
    }
  }

  async function open(nextMode, title, label, panel = null) {
    mode = nextMode;
    target = panel && {
      nodeId: panel.scope, panelId: panel.panelId, label: panel.panelLabel || panel.panelId,
    };
    heading.textContent = target ? `${title} · ${target.label}` : title;
    submit.textContent = label;
    for (const section of ["create", "edit", "remove"]) field(section).hidden = section !== mode;
    status.textContent = "";
    id.required = mode === "create";
    id.value = "";
    scope.value = "0";
    file.value = "index.html";
    revision = null;
    editLockError = "";
    busy = Boolean(acquireEdit);
    sync();
    dialog.showModal();
    if (acquireEdit) {
      status.textContent = "Opening sheet for editing…";
      try {
        await pendingRelease;
        const lock = await acquireEdit();
        editSessionId = lock.sessionId;
        if (!lock.ok) editLockError = `Read-only · ${lock.error}`;
      } catch {
        editLockError = "Read-only · Could not open the sheet for editing.";
      }
      busy = false;
      if (leaving) {
        release(true);
        return;
      }
    }
    if (mode === "edit") {
      await loadFile();
    } else {
      status.textContent = editLockError;
      sync();
      if (mode === "create") id.focus();
      else field("close").focus();
    }
  }

  add.hidden = false;
  add.addEventListener("click", () => {
    void open("create", "Add dashboard", "Create dashboard");
  });

  window.addEventListener("workspace-edit-lock", ({ detail: { editLock } }) => {
    if (!editSessionId || editLock?.session_id === editSessionId) return;
    editSessionId = null;
    editLockError = editLock?.locked
      ? `Read-only · This sheet is locked for editing by ${editLock.owner}.`
      : "Read-only · This editing session is no longer active.";
    status.textContent = editLockError;
    sync();
  });
  window.addEventListener("pagehide", () => {
    leaving = true;
    release(true);
  });
  window.addEventListener("pageshow", () => {
    if (leaving && acquireEdit && dialog.open && !editSessionId) {
      editLockError ||= "Read-only · Reopen the dashboard editor to edit this sheet.";
      status.textContent = editLockError;
      sync();
    }
    leaving = false;
  });
  field("close").addEventListener("click", () => {
    if (canDiscard()) close();
  });
  dialog.addEventListener("cancel", event => {
    event.preventDefault();
    if (!busy && canDiscard()) close();
  });
  source.addEventListener("input", sync);
  file.addEventListener("change", () => {
    if (canDiscard()) loadFile();
    else file.value = selectedFile;
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (submit.disabled) return;
    busy = true;
    sync();
    status.textContent = "";
    try {
      if (mode === "create") {
        const nodeId = scopes[Number(scope.value)].nodeId;
        const panelId = id.value;
        await request(nodeId, { action: "create", panel: panelId }, editSessionId);
        await onCreate(nodeId, panelId);
        close();
      } else if (mode === "remove") {
        await request(target.nodeId, { action: "remove", panel: target.panelId }, editSessionId);
        close();
        onRemove(target);
      } else {
        const result = await request(target.nodeId, {
          action: "save", panel: target.panelId, name: selectedFile, revision, source: source.value,
        }, editSessionId);
        revision = result.revision;
        saved = source.value;
        status.textContent = "Saved.";
        onSave(target);
      }
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false;
      sync();
    }
  });

  return {
    edit(panel) {
      void open("edit", "Edit dashboard", "Save", panel);
    },
    remove(panel) {
      void open("remove", "Remove dashboard", "Remove dashboard", panel);
      field("remove").textContent = `Permanently remove the source files for “${target.label}”?`;
    },
  };
}
