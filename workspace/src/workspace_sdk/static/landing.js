const list = document.querySelector("#registered-workspaces");
const message = document.querySelector("#workspace-list-message");
const dialog = document.querySelector("#create-workspace");
const form = dialog.querySelector("form");
const path = form.elements.path;
const submit = form.querySelector('[type="submit"]');
const cancel = document.querySelector("#cancel-workspace");
const status = document.querySelector("#create-workspace-status");
const problem = document.querySelector("#create-workspace-error");

async function refresh() {
  try {
    const response = await fetch("/workspaces");
    if (!response.ok) throw new Error("Could not load the Workspace list. Reload to try again.");
    const { workspaces } = await response.json();
    list.replaceChildren(...workspaces.map(workspace => {
      const item = document.createElement("li");
      const link = document.createElement("a");
      const detail = document.createElement("span");
      link.href = `/workspaces/${workspace.id}/`;
      link.textContent = workspace.name;
      detail.textContent = workspace.path;
      item.append(link, detail);
      return item;
    }));
    message.textContent = workspaces.length ? "" : "Create a Workspace to get started.";
    message.hidden = Boolean(workspaces.length);
  } catch (error) {
    message.hidden = false;
    message.textContent = error.message;
  }
}

document.querySelector("#new-workspace").addEventListener("click", () => {
  form.reset();
  problem.hidden = true;
  dialog.showModal();
});
cancel.addEventListener("click", () => dialog.close());
dialog.addEventListener("cancel", event => {
  if (submit.disabled) event.preventDefault();
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (submit.disabled) return;
  path.readOnly = cancel.disabled = submit.disabled = true;
  problem.hidden = true;
  status.textContent = "Creating the Workspace and its Python environment…";
  try {
    const response = await fetch("/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path.value }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not create the Workspace.");
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
