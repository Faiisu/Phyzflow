const list = document.querySelector("#registered-workspaces");
const message = document.querySelector("#workspace-list-message");
const listError = document.querySelector("#workspace-list-error");
const dialog = document.querySelector("#create-workspace");
const form = dialog.querySelector("form");
const path = form.elements.path;
const title = document.querySelector("#create-workspace-title");
const label = document.querySelector('label[for="workspace-path"]');
const hint = document.querySelector("#workspace-path-hint");
const submit = form.querySelector('[type="submit"]');
const cancel = document.querySelector("#cancel-workspace");
const status = document.querySelector("#create-workspace-status");
const problem = document.querySelector("#create-workspace-error");

let action = "create";

async function deleteWorkspace(workspace, button) {
  if (button.disabled) return;
  button.disabled = true;
  listError.hidden = true;
  listError.textContent = "";
  try {
    const response = await fetch(`/workspaces/${workspace.id}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Could not delete ${workspace.name}.`);
    await refresh();
  } catch (error) {
    button.disabled = false;
    listError.textContent = error.message;
    listError.hidden = false;
  }
}

async function refresh() {
  try {
    const response = await fetch("/workspaces");
    if (!response.ok) throw new Error("Could not load the Workspace list. Reload to try again.");
    const { workspaces } = await response.json();
    listError.hidden = true;
    listError.textContent = "";
    list.replaceChildren(...workspaces.map(workspace => {
      const item = document.createElement("li");
      const details = document.createElement("div");
      const link = document.createElement("a");
      const detail = document.createElement("span");
      const remove = document.createElement("button");
      details.className = "workspace-entry-details";
      link.href = `/workspaces/${workspace.id}/`;
      link.textContent = workspace.name;
      detail.textContent = workspace.path;
      details.append(link, detail);
      remove.type = "button";
      remove.className = "delete-workspace";
      remove.textContent = "Delete";
      remove.setAttribute("aria-label", `Delete ${workspace.name}`);
      remove.addEventListener("click", () => deleteWorkspace(workspace, remove));
      item.append(details, remove);
      return item;
    }));
    message.textContent = workspaces.length ? "" : "Create or add a Workspace to get started.";
    message.hidden = Boolean(workspaces.length);
  } catch (error) {
    message.hidden = false;
    message.textContent = error.message;
  }
}

function openDialog(nextAction) {
  action = nextAction;
  const adding = action === "add";
  form.reset();
  title.textContent = adding ? "Add Workspace" : "Create Workspace";
  label.textContent = adding ? "Existing folder path" : "New folder path";
  hint.textContent = adding
    ? "Enter the absolute path to an existing project folder on the Phyzflow host."
    : "Enter an absolute path on the Phyzflow host. The parent folder must already exist.";
  submit.textContent = adding ? "Add" : "Create";
  problem.hidden = true;
  dialog.showModal();
}

document.querySelector("#new-workspace").addEventListener("click", () => openDialog("create"));
document.querySelector("#add-workspace").addEventListener("click", () => openDialog("add"));
cancel.addEventListener("click", () => dialog.close());
dialog.addEventListener("cancel", event => {
  if (submit.disabled) event.preventDefault();
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (submit.disabled) return;
  path.readOnly = cancel.disabled = submit.disabled = true;
  problem.hidden = true;
  status.textContent = action === "add"
    ? "Checking the project’s Python environment…"
    : "Creating the Workspace and its Python environment…";
  try {
    const response = await fetch("/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path.value, action }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Could not ${action} the Workspace.`);
    await refresh();
    dialog.close();
  } catch (error) {
    problem.textContent = error.message;
    problem.hidden = false;
  } finally {
    status.textContent = "";
    path.readOnly = cancel.disabled = submit.disabled = false;
  }
});

refresh();
