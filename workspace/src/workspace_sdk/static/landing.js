const list = document.querySelector("#registered-workspaces");
const message = document.querySelector("#workspace-list-message");
const listError = document.querySelector("#workspace-list-error");
const listTitle = document.querySelector("#workspace-list-title");
const filterBar = document.querySelector("#workspace-filter-bar");
const filterInput = document.querySelector("#workspace-filter");
const dialog = document.querySelector("#create-workspace");
const form = dialog.querySelector("form");
const path = form.elements.path;
const title = document.querySelector("#create-workspace-title");
const label = document.querySelector('label[for="workspace-path"]');
const hint = document.querySelector("#workspace-path-hint");
const folderBrowser = document.querySelector("#workspace-folder-browser");
const folderList = document.querySelector("#workspace-folders");
const folderEmpty = document.querySelector("#workspace-folder-empty");
const locationLabel = document.querySelector("#workspace-location-label");
const locationPath = document.querySelector("#workspace-location");
const home = document.querySelector("#workspace-folder-home");
const up = document.querySelector("#workspace-folder-up");
const nameField = document.querySelector("#workspace-folder-name-field");
const folderName = document.querySelector("#workspace-folder-name");
const manualField = document.querySelector("#workspace-manual-path-field");
const pathMode = document.querySelector("#workspace-path-mode");
const addModes = document.querySelector("#workspace-add-modes");
const addBrowse = document.querySelector("#workspace-add-browse");
const addPath = document.querySelector("#workspace-add-path");
const submit = form.querySelector('[type="submit"]');
const cancel = document.querySelector("#cancel-workspace");
const status = document.querySelector("#create-workspace-status");
const problem = document.querySelector("#create-workspace-error");
const deleteDialog = document.querySelector("#delete-workspace");
const deleteForm = deleteDialog.querySelector("form");
const deleteSubmit = deleteForm.querySelector('[type="submit"]');
const deleteCancel = document.querySelector("#cancel-delete-workspace");
const deleteStatus = document.querySelector("#delete-workspace-status");

let action = "create";
let allWorkspaces = [];
let selectedWorkspace;
let folder;
let folderRequest;
let saving = false;

function renderWorkspaces() {
  const query = filterInput.value.trim().toLowerCase();
  const visible = query
    ? allWorkspaces.filter(item => item.name.toLowerCase().includes(query) || item.path.toLowerCase().includes(query))
    : allWorkspaces;
  list.replaceChildren(...visible.map(workspace => {
    const item = document.createElement("li");
    const details = document.createElement("div");
    const link = document.createElement("a");
    const arrow = document.createElement("span");
    const detail = document.createElement("span");
    const remove = document.createElement("button");
    details.className = "workspace-entry-details";
    link.href = `/workspaces/${workspace.id}/`;
    link.textContent = workspace.name;
    arrow.textContent = "→";
    arrow.setAttribute("aria-hidden", "true");
    link.append(" ", arrow);
    detail.textContent = workspace.path;
    details.append(link, detail);
    remove.type = "button";
    remove.className = "delete-workspace";
    remove.textContent = "Delete";
    remove.setAttribute("aria-label", `Delete ${workspace.name}`);
    remove.addEventListener("click", () => {
      selectedWorkspace = workspace;
      document.querySelector("#delete-workspace-name").textContent = workspace.name;
      document.querySelector("#delete-workspace-path").textContent = workspace.path;
      listError.hidden = true;
      deleteDialog.showModal();
    });
    item.append(details, remove);
    return item;
  }));
  if (!allWorkspaces.length) {
    message.textContent = "No Workspaces yet. Create a Workspace or add an existing project folder.";
    message.hidden = false;
  } else if (!visible.length) {
    message.textContent = "No Workspaces match this filter. Try a different name or path.";
    message.hidden = false;
  } else {
    message.textContent = "";
    message.hidden = true;
  }
}

deleteCancel.addEventListener("click", () => deleteDialog.close());
deleteDialog.addEventListener("cancel", event => {
  if (deleteSubmit.disabled) event.preventDefault();
});

deleteForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (deleteSubmit.disabled) return;
  deleteCancel.disabled = deleteSubmit.disabled = true;
  deleteStatus.textContent = "Stopping running flows and moving the folder to Trash…";
  const workspace = selectedWorkspace;
  try {
    const response = await fetch(`/workspaces/${workspace.id}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Could not delete ${workspace.name}.`);
    await refresh();
    deleteDialog.close();
    (list.querySelector("a") || listTitle).focus();
  } catch (error) {
    deleteDialog.close();
    listError.textContent = error.message;
    listError.hidden = false;
    listError.focus();
  } finally {
    deleteStatus.textContent = "";
    deleteCancel.disabled = deleteSubmit.disabled = false;
  }
});

async function refresh() {
  try {
    const response = await fetch("/workspaces");
    if (!response.ok) throw new Error("Could not load the Workspace list. Reload to try again.");
    const { workspaces } = await response.json();
    allWorkspaces = workspaces;
    listError.hidden = true;
    listError.textContent = "";
    filterBar.hidden = workspaces.length < 2;
    if (workspaces.length < 2) filterInput.value = "";
    renderWorkspaces();
  } catch (error) {
    message.hidden = false;
    message.textContent = error.message;
  }
}

function updateFolderControls() {
  const manual = !manualField.hidden;
  const loading = Boolean(folderRequest);
  folderBrowser.hidden = manual;
  folderBrowser.disabled = loading || saving;
  up.disabled = !folder?.parent;
  nameField.hidden = manual || action === "add";
  folderName.disabled = nameField.hidden || saving;
  path.disabled = !manual || saving;
  pathMode.disabled = cancel.disabled = saving;
  addBrowse.disabled = addPath.disabled = saving;
  submit.disabled = saving || (manual ? action === "add" && !path.value.trim() : loading || !folder);
}

function showPathMode(manual) {
  const adding = action === "add";
  manualField.hidden = !manual;
  pathMode.textContent = manual ? "Browse folders" : "Enter a folder path";
  addBrowse.setAttribute("aria-pressed", String(!manual));
  addPath.setAttribute("aria-pressed", String(manual));
  hint.textContent = manual
    ? "Enter an absolute folder path on the Phyzflow host."
    : adding
      ? "Choose an existing project folder on the Phyzflow host."
      : "Choose a parent folder on the Phyzflow host and enter a name for the new folder.";
  updateFolderControls();
}

async function browseFolders(destination, moveFocus = false) {
  const request = new AbortController();
  folderRequest = request;
  problem.hidden = true;
  status.textContent = "Loading folders…";
  updateFolderControls();
  try {
    const response = await fetch("/workspace-folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(destination ? { path: destination } : {}),
      signal: request.signal,
    });
    const selection = await response.json();
    if (folderRequest !== request) return;
    if (!response.ok) throw new Error(selection.error || "Could not load folders.");
    folder = selection;
    locationPath.textContent = folder.path;
    folderEmpty.hidden = folder.folders.length > 0;
    folderList.replaceChildren(...folder.folders.map(entry => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = entry.name;
      button.addEventListener("click", () => browseFolders(entry.path, true));
      item.append(button);
      return item;
    }));
    folderList.scrollTop = 0;
    if (moveFocus) locationPath.focus({ preventScroll: true });
  } catch (error) {
    if (folderRequest !== request) return;
    problem.textContent = error.message;
    problem.hidden = false;
    if (moveFocus) problem.focus();
  } finally {
    if (folderRequest === request) {
      folderRequest = undefined;
      status.textContent = "";
      updateFolderControls();
    }
  }
}

function stopBrowsing() {
  folderRequest?.abort();
  folderRequest = undefined;
}

function openDialog(nextAction) {
  stopBrowsing();
  action = nextAction;
  const adding = action === "add";
  dialog.dataset.action = action;
  pathMode.hidden = adding;
  addModes.hidden = !adding;
  form.reset();
  folder = undefined;
  locationPath.textContent = "";
  folderList.replaceChildren();
  folderEmpty.hidden = true;
  title.textContent = adding ? "Add Workspace" : "Create Workspace";
  locationLabel.textContent = adding ? "Folder to add" : "Parent folder";
  label.textContent = adding ? "Existing folder path" : "New folder path";
  submit.textContent = adding ? "Add" : "Create";
  problem.hidden = true;
  showPathMode(false);
  browseFolders();
  dialog.showModal();
  title.focus();
}

home.addEventListener("click", () => browseFolders(undefined, true));
up.addEventListener("click", () => browseFolders(folder.parent, true));
function changePathMode(manual) {
  if (manual === !manualField.hidden) return;
  stopBrowsing();
  problem.hidden = true;
  status.textContent = "";
  showPathMode(manual);
  if (manual) path.focus();
  else if (!folder) browseFolders();
  else locationPath.focus();
}
pathMode.addEventListener("click", () => changePathMode(manualField.hidden));
addBrowse.addEventListener("click", () => changePathMode(false));
addPath.addEventListener("click", () => changePathMode(true));
path.addEventListener("input", updateFolderControls);
filterInput.addEventListener("input", renderWorkspaces);
document.querySelector("#new-workspace").addEventListener("click", () => openDialog("create"));
document.querySelector("#add-workspace").addEventListener("click", () => openDialog("add"));
cancel.addEventListener("click", () => dialog.close());
dialog.addEventListener("close", () => {
  if (!dialog.open) stopBrowsing();
});
dialog.addEventListener("cancel", event => {
  if (saving) event.preventDefault();
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (submit.disabled) return;
  const destination = !manualField.hidden
    ? { path: path.value }
    : action === "add"
      ? { folder: folder.path }
      : { parent: folder.path, name: folderName.value };
  saving = true;
  updateFolderControls();
  problem.hidden = true;
  status.textContent = action === "add"
    ? "Checking the project’s Python environment…"
    : "Creating the Workspace and its Python environment…";
  try {
    const response = await fetch("/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...destination, action }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Could not ${action} the Workspace.`);
    filterInput.value = "";
    await refresh();
    dialog.close();
    (list.querySelector(`a[href="/workspaces/${data.id}/"]`) || listTitle).focus();
  } catch (error) {
    problem.textContent = error.message;
    problem.hidden = false;
    problem.focus();
  } finally {
    status.textContent = "";
    saving = false;
    updateFolderControls();
  }
});

refresh();
