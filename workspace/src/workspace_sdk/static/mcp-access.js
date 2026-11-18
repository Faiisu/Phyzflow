const dialog = document.querySelector("#mcp-access");
const form = dialog.querySelector("form");
const choice = form.elements.identity;
const details = document.querySelector("#mcp-client-details");
const scopes = document.querySelector("#mcp-workspaces");
const workspaceOptions = document.querySelector("#mcp-workspace-options");
const issued = document.querySelector("#mcp-issued-token");
const secret = document.querySelector("#mcp-secret");
const status = document.querySelector("#mcp-access-status");
const problem = document.querySelector("#mcp-access-error");
const close = document.querySelector("#close-mcp-access");
const revoke = document.querySelector("#revoke-mcp-token");
const save = document.querySelector("#save-mcp-token");
let tokens = [];
let workspaces = [];
let busy = false;

function clearSecret() {
  secret.value = "";
  issued.hidden = true;
}

function showSelection() {
  const token = tokens.find(item => item.id === choice.value);
  const selected = new Set(token?.workspace_ids);
  details.hidden = Boolean(token);
  form.elements.name.required = !token;
  revoke.hidden = !token;
  save.textContent = token ? "Save grants" : "Issue token";
  workspaceOptions.replaceChildren(...workspaces.map(workspace => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    const text = document.createElement("span");
    input.type = "checkbox";
    input.name = "workspace_ids";
    input.value = workspace.id;
    input.checked = selected.has(workspace.id);
    text.textContent = workspace.name;
    text.title = workspace.path;
    label.append(input, text);
    return label;
  }));
  if (!workspaces.length) workspaceOptions.textContent = "No registered Workspaces.";
}

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Could not update MCP access.");
  return data;
}

function showTokens(identity = "") {
  choice.replaceChildren(new Option("New token", ""), ...tokens.map(token => new Option(token.name, token.id)));
  choice.value = identity;
  showSelection();
}

async function perform(update) {
  if (busy) return;
  busy = true;
  choice.disabled = scopes.disabled = close.disabled = revoke.disabled = save.disabled = true;
  problem.hidden = true;
  status.textContent = "";
  try {
    await update();
  } catch (error) {
    problem.textContent = error.message;
    problem.hidden = false;
  } finally {
    busy = false;
    choice.disabled = scopes.disabled = close.disabled = revoke.disabled = save.disabled = false;
  }
}

document.querySelector("#open-mcp-access").addEventListener("click", () => {
  clearSecret();
  tokens = [];
  workspaces = [];
  form.reset();
  showTokens();
  dialog.showModal();
  perform(async () => {
    ({ tokens, workspaces } = await request("/mcp-tokens"));
    showTokens();
  });
});
close.addEventListener("click", () => dialog.close());
dialog.addEventListener("close", clearSecret);
dialog.addEventListener("cancel", event => {
  if (busy) event.preventDefault();
});
choice.addEventListener("change", () => {
  clearSecret();
  status.textContent = "";
  problem.hidden = true;
  showSelection();
});
form.addEventListener("submit", event => {
  event.preventDefault();
  const workspace_ids = new FormData(form).getAll("workspace_ids");
  const identity = choice.value;
  const name = form.elements.name.value;
  perform(async () => {
    clearSecret();
    if (identity) {
      await request(`/mcp-tokens/${identity}`, { workspace_ids });
      tokens.find(item => item.id === identity).workspace_ids = workspace_ids;
      status.textContent = "Grants saved.";
    } else {
      const { id, token } = await request("/mcp-tokens", { name, workspace_ids });
      tokens.push({ id, name: name.trim(), workspace_ids });
      form.elements.name.value = "";
      showTokens(id);
      secret.value = token;
      issued.hidden = false;
      secret.focus();
      secret.select();
    }
  });
});
revoke.addEventListener("click", () => perform(async () => {
  const identity = choice.value;
  await request(`/mcp-tokens/${identity}`, { action: "revoke" });
  clearSecret();
  tokens = tokens.filter(item => item.id !== identity);
  showTokens();
  status.textContent = "Token revoked.";
}));
