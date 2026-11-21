export function initDashboardEditor({ scopes, request, onCreate, onSave, onRemove }) {
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
    id.disabled = busy;
    scope.disabled = busy || scopes.length === 1;
    submit.disabled = busy || (mode === "edit" && !dirty());
    field("close").disabled = busy;
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
      status.textContent = revision === "missing" ? "Save to create this file." : "";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false;
      sync();
    }
  }

  function open(nextMode, title, label, panel = null) {
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
    sync();
    dialog.showModal();
  }

  add.hidden = false;
  add.addEventListener("click", () => {
    open("create", "Add dashboard", "Create dashboard");
    id.focus();
  });

  field("close").addEventListener("click", () => {
    if (canDiscard()) dialog.close();
  });
  dialog.addEventListener("cancel", event => {
    if (busy || !canDiscard()) event.preventDefault();
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
        await request(nodeId, { action: "create", panel: panelId });
        dialog.close();
        onCreate(nodeId, panelId);
      } else if (mode === "remove") {
        await request(target.nodeId, { action: "remove", panel: target.panelId });
        dialog.close();
        onRemove(target);
      } else {
        const result = await request(target.nodeId, {
          action: "save", panel: target.panelId, name: selectedFile, revision, source: source.value,
        });
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
      open("edit", "Edit dashboard", "Save", panel);
      loadFile();
    },
    remove(panel) {
      open("remove", "Remove dashboard", "Remove dashboard", panel);
      field("remove").textContent = `Permanently remove the source files for “${target.label}”?`;
      field("close").focus();
    },
  };
}
