const dialog = document.querySelector("#workspace-files");
const up = dialog.querySelector("#workspace-files-up");
const location = dialog.querySelector("#workspace-files-path");
const drop = dialog.querySelector("#workspace-files-drop");
const list = dialog.querySelector("#workspace-files-list");
const empty = dialog.querySelector("#workspace-files-empty");
const choose = dialog.querySelector("#choose-workspace-files");
const input = dialog.querySelector("#workspace-file-input");
const chooseFolder = dialog.querySelector("#choose-workspace-folder");
const folderInput = dialog.querySelector("#workspace-folder-input");
const form = dialog.querySelector("#workspace-folder-form");
const folderName = dialog.querySelector("#workspace-files-folder");
const create = form.querySelector("button");
const status = dialog.querySelector("#workspace-files-status");
const error = dialog.querySelector("#workspace-files-error");
let folder = ".";
let busy = false;
let loaded = false;
let dropTarget;

async function request(route, path, options, name) {
  const query = new URLSearchParams({ path });
  if (name !== undefined) query.set("name", name);
  const response = await fetch(`./files${route}?${query}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error ?? `Workspace file request failed (${response.status}).`);
  return data;
}

function setBusy(value) {
  busy = value;
  up.disabled = busy || folder === ".";
  choose.disabled = chooseFolder.disabled = folderName.disabled = create.disabled = busy || !loaded;
  for (const button of list.querySelectorAll("button")) button.disabled = busy;
}

async function refresh(path) {
  const { entries } = await request("", path);
  folder = path;
  loaded = true;
  location.textContent = path === "." ? "Files" : `Files / ${path}`;
  empty.hidden = entries.length > 0;
  list.replaceChildren(...entries.map(entry => {
    const row = document.createElement("li");
    const item = document.createElement(entry.directory ? "button" : "span");
    const name = document.createElement("span");
    name.textContent = entry.name;
    item.className = "workspace-file-row";
    if (entry.directory) {
      const path = folder === "." ? entry.name : `${folder}/${entry.name}`;
      const icon = document.createElement("span");
      icon.className = "folder-icon";
      icon.setAttribute("aria-hidden", "true");
      item.type = "button";
      item.dataset.folder = path;
      item.disabled = busy;
      item.append(icon, name);
      item.addEventListener("click", () => openFolder(path, true));
    } else {
      const size = document.createElement("span");
      size.className = "workspace-file-size";
      size.textContent = `${entry.size.toLocaleString("en")} B`;
      item.append(name, size);
    }
    row.append(item);
    return row;
  }));
}

function showError(message) {
  error.textContent = message;
  error.hidden = !message;
}

async function run(message, operation) {
  if (busy) return;
  setBusy(true);
  showError("");
  status.textContent = message;
  try {
    await operation();
  } catch (failure) {
    showError(failure.message);
  } finally {
    if (status.textContent === message) status.textContent = "";
    setBusy(false);
  }
}

function openFolder(path, focus = false) {
  return run("Loading folder…", async () => {
    await refresh(path);
    if (focus && dialog.open) location.focus();
  });
}

function createFolder(path, name) {
  return request("/folders", path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
  });
}

function folderSelection(files) {
  const root = [];
  const folders = new Map([["", root]]);
  for (const file of files) {
    const parts = file.webkitRelativePath.split("/");
    const name = parts.pop();
    let path = "";
    let children = root;
    for (const part of parts) {
      path += `/${part}`;
      if (!folders.has(path)) {
        const entry = { name: part, children: [] };
        children.push(entry);
        folders.set(path, entry.children);
      }
      children = folders.get(path);
    }
    children.push({ name, file });
  }
  return root;
}

async function droppedEntry(entry) {
  if (entry instanceof File) return { name: entry.name, file: entry };
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    return { name: entry.name, file };
  }
  const reader = entry.createReader();
  const children = [];
  for (;;) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) return { name: entry.name, children };
    for (const child of batch) children.push(await droppedEntry(child));
  }
}

function upload(getEntries, path) {
  if (!loaded) return;
  return run("Preparing upload…", async () => {
    const failures = [];
    let files = 0;
    let folders = 0;
    async function uploadEntries(entries, parent) {
      for (const entry of entries) {
        const destination = parent === "." ? entry.name : `${parent}/${entry.name}`;
        status.textContent = `Uploading ${destination}…`;
        try {
          if (entry.children) {
            await createFolder(parent, entry.name);
            folders++;
            await uploadEntries(entry.children, destination);
          } else {
            await request("/upload", parent, {
              method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: entry.file,
            }, entry.name);
            files++;
          }
        } catch (failure) {
          failures.push(`${destination}: ${failure.message}`);
        }
      }
    }
    await uploadEntries(await getEntries(), path);
    const uploaded = [];
    if (folders) uploaded.push(`${folders} ${folders === 1 ? "folder" : "folders"}`);
    if (files) uploaded.push(`${files} ${files === 1 ? "file" : "files"}`);
    status.textContent = uploaded.length ? `${uploaded.join(" and ")} uploaded.` : "";
    try {
      await refresh(folder);
    } catch (failure) {
      failures.push(failure.message);
    }
    showError(failures.join("\n"));
  });
}

document.querySelector("#open-workspace-files").addEventListener("click", () => {
  dialog.showModal();
  openFolder(folder);
});
dialog.querySelector("#close-workspace-files").addEventListener("click", () => dialog.close());
up.addEventListener("click", () => openFolder(folder.split("/").slice(0, -1).join("/") || ".", true));
choose.addEventListener("click", () => input.click());
input.addEventListener("change", () => {
  const files = [...input.files];
  upload(() => files.map(file => ({ name: file.name, file })), folder);
  input.value = "";
});
chooseFolder.addEventListener("click", () => folderInput.click());
folderInput.addEventListener("change", () => {
  const files = [...folderInput.files];
  upload(() => folderSelection(files), folder);
  folderInput.value = "";
});
form.addEventListener("submit", event => {
  event.preventDefault();
  run("Creating folder…", async () => {
    await createFolder(folder, folderName.value);
    folderName.value = "";
    status.textContent = "Folder created.";
    await refresh(folder);
  });
});

function highlight(target) {
  if (target === dropTarget) return;
  dropTarget?.classList.remove("is-drop-target");
  target?.classList.add("is-drop-target");
  dropTarget = target;
}

dialog.addEventListener("dragover", event => {
  if (!event.dataTransfer.types.includes("Files")) return;
  event.preventDefault();
  const target = !busy && loaded && drop.contains(event.target)
    ? event.target.closest("[data-folder]") ?? drop : null;
  highlight(target);
  event.dataTransfer.dropEffect = target ? "copy" : "none";
});
dialog.addEventListener("dragleave", event => {
  if (!dialog.contains(event.relatedTarget)) highlight(null);
});
dialog.addEventListener("drop", event => {
  if (!event.dataTransfer.types.includes("Files")) return;
  event.preventDefault();
  highlight(null);
  if (!busy && loaded && drop.contains(event.target)) {
    const items = [...event.dataTransfer.items].filter(item => item.kind === "file");
    const entries = items.length
      ? items.map(item => item.webkitGetAsEntry?.() ?? item.getAsFile()).filter(Boolean)
      : [...event.dataTransfer.files];
    upload(async () => {
      const selected = [];
      for (const entry of entries) selected.push(await droppedEntry(entry));
      return selected;
    }, event.target.closest("[data-folder]")?.dataset.folder ?? folder);
  }
});
dialog.addEventListener("close", () => highlight(null));
