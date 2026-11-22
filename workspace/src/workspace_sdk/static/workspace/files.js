const dialog = document.querySelector("#workspace-files");
const up = dialog.querySelector("#workspace-files-up");
const location = dialog.querySelector("#workspace-files-path");
const drop = dialog.querySelector("#workspace-files-drop");
const list = dialog.querySelector("#workspace-files-list");
const empty = dialog.querySelector("#workspace-files-empty");
const choose = dialog.querySelector("#choose-workspace-files");
const input = dialog.querySelector("#workspace-file-input");
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
  choose.disabled = folderName.disabled = create.disabled = busy || !loaded;
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

function upload(files, path) {
  if (!files.length || !loaded) return;
  return run("Uploading files…", async () => {
    const failures = [];
    let uploaded = 0;
    for (const file of files) {
      status.textContent = `Uploading ${file.name}…`;
      try {
        await request("/upload", path, {
          method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file,
        }, file.name);
        uploaded++;
      } catch (failure) {
        failures.push(`${file.name}: ${failure.message}`);
      }
    }
    status.textContent = uploaded ? `${uploaded} ${uploaded === 1 ? "file" : "files"} uploaded.` : "";
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
  upload([...input.files], folder);
  input.value = "";
});
form.addEventListener("submit", event => {
  event.preventDefault();
  run("Creating folder…", async () => {
    await request("/folders", folder, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: folderName.value }),
    });
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
  if (drop.contains(event.target)) {
    upload([...event.dataTransfer.files], event.target.closest("[data-folder]")?.dataset.folder ?? folder);
  }
});
dialog.addEventListener("close", () => highlight(null));
