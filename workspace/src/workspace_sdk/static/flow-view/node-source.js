import { element, highlightCode } from "./display.js";

export function initNodeSource({ recoveryView, getStopPhase, isRunInProgress }) {
  const editLockBadge = document.querySelector("#edit-lock-badge");
  const nodeFilesDialog = document.querySelector("#node-files");
  const nodeFilesHeading = document.querySelector("#node-files-heading");
  const nodeFilesPath = document.querySelector("#node-files-path");
  const nodeFilesTree = document.querySelector("#node-files-tree");
  const nodeFilesMessage = document.querySelector("#node-files-message");
  const nodeSourceForm = document.querySelector("#node-source-form");
  const nodeSourceInput = document.querySelector("#node-source-input");
  const nodeCodeEditor = document.querySelector("#node-code-editor");
  const nodeSourceHighlight = document.querySelector("#node-source-highlight");
  const nodeSourceName = document.querySelector("#node-source-name");
  const nodeSourceMessage = document.querySelector("#node-source-message");
  const forceOpenNodeSource = document.querySelector("#force-open-node-source");
  const saveNodeSource = document.querySelector("#save-node-source");
  let editRunningSource = false;
  let sheetEditLock = null;
  let nodeFilesEditLock = null;

  function sourceLanguage(path) {
    if (path.endsWith(".py")) return "python";
    if (path.endsWith(".json")) return "json";
    return null;
  }

  function syncSourceScroll() {
    nodeSourceHighlight.style.width = `${nodeSourceInput.clientWidth + 2}px`;
    nodeSourceHighlight.style.height = `${nodeSourceInput.clientHeight + 2}px`;
    nodeSourceHighlight.scrollTop = nodeSourceInput.scrollTop;
    nodeSourceHighlight.scrollLeft = nodeSourceInput.scrollLeft;
  }

  let highlightFrame;
  function highlightSource() {
    if (!currentSource) return;
    const text = nodeSourceInput.value;
    const preview = text.endsWith("\n") ? `${text}\n` : text;
    const highlighted = highlightCode(nodeSourceHighlight.firstElementChild, preview, sourceLanguage(currentSource.path));
    nodeCodeEditor.toggleAttribute("data-highlighted", highlighted);
    syncSourceScroll();
  }

  nodeSourceInput.addEventListener("scroll", syncSourceScroll);
  new ResizeObserver(syncSourceScroll).observe(nodeSourceInput);

  let nodeFilesRequest;
  let nodeSourceRequest;
  let currentSource = null;
  let savingSource = false;

  function currentEditSessionId() {
    if (nodeFilesEditLock?.sessionId) return nodeFilesEditLock.sessionId;
    if (window.parent !== window) return window.parent.phyzflowEditSessionId?.(window) ?? null;
    return null;
  }

  function formatHeartbeat(timestamp) {
    if (typeof timestamp !== "string" || !Number.isFinite(Date.parse(timestamp))) return "Unavailable";
    return timestamp.replace(/\.\d+/, "").replace("T", " ").replace(/(?:\+00:00|Z)$/, " UTC");
  }

  function externalEditLockMessage() {
    if (sheetEditLock?.locked && !sheetEditLock.owned_by_viewer) {
      const state = sheetEditLock.disconnected
        ? "Possibly stale (Disconnected) · "
        : sheetEditLock.stale
          ? "Possibly stale · "
          : "";
      return `${state}Locked for editing by ${sheetEditLock.owner} · Last heartbeat ${formatHeartbeat(sheetEditLock.last_heartbeat)}`;
    }
    if (nodeFilesEditLock && !nodeFilesEditLock.ok) return nodeFilesEditLock.error;
    return "";
  }

  function syncEditLockUI() {
    const lockedByOther = Boolean(sheetEditLock?.locked && !sheetEditLock.owned_by_viewer);
    if (editLockBadge) {
      editLockBadge.hidden = !lockedByOther;
      editLockBadge.textContent = lockedByOther && sheetEditLock?.stale ? "Read-only · Possibly stale" : "Read-only";
      editLockBadge.title = lockedByOther ? externalEditLockMessage() : "";
    }
    forceOpenNodeSource.hidden = !(lockedByOther && window.parent !== window && window.parent.phyzflowRequestForceOpen);
    if (currentSource && !savingSource && !nodeSourceRequest) {
      nodeSourceInput.readOnly = nodeSourceLocked();
      updateSourceDirty();
      const message = externalEditLockMessage();
      if (message) nodeSourceMessage.textContent = `Read-only · ${message}`;
    }
  }

  forceOpenNodeSource.addEventListener("click", () => {
    if (window.parent !== window) window.parent.phyzflowRequestForceOpen?.(window);
  });

  window.addEventListener("workspace-edit-lock", async event => {
    sheetEditLock = event.detail.editLock ?? null;
    const unlocked = !sheetEditLock?.locked || sheetEditLock.owned_by_viewer;
    if (unlocked && nodeFilesEditLock && !nodeFilesEditLock.ok) {
      nodeFilesEditLock = null;
    }
    syncEditLockUI();
    if (nodeFilesDialog.open && !sheetEditLock?.locked && !currentEditSessionId() &&
        window.parent !== window && window.parent.phyzflowAcquireEditLock) {
      nodeFilesEditLock = await window.parent.phyzflowAcquireEditLock(window);
      if (nodeFilesEditLock.editLock) sheetEditLock = nodeFilesEditLock.editLock;
      if (nodeFilesEditLock.ok && currentSource && !savingSource) {
        await openSource(currentSource.index, currentSource.path);
      } else {
        syncEditLockUI();
      }
    }
  });

  function sourceIsDirty() {
    return currentSource !== null && nodeSourceInput.value !== currentSource.text;
  }

  function nodeSourceLocked() {
    return getStopPhase() === "stopping" ||
      Boolean(externalEditLockMessage()) ||
      (isRunInProgress() && !(editRunningSource && currentSource?.path.endsWith(".py")));
  }

  function updateSourceDirty() {
    const dirty = sourceIsDirty();
    document.documentElement.toggleAttribute("data-unsaved-source", dirty || savingSource);
    saveNodeSource.disabled = (!dirty && currentSource?.revision !== "missing") ||
      savingSource || Boolean(nodeSourceRequest) || nodeSourceLocked();
  }

  function canLeaveSource() {
    return !savingSource && (!sourceIsDirty() || window.confirm("Discard unsaved changes to this file?"));
  }

  function releaseNodeFilesLock() {
    nodeFilesEditLock = null;
    if (window.parent !== window) window.parent.phyzflowReleaseEditLock?.(window);
  }

  window.addEventListener("beforeunload", event => {
    if (sourceIsDirty()) {
      event.preventDefault();
      event.returnValue = true;
    }
  });
  window.addEventListener("pagehide", releaseNodeFilesLock);
  document.querySelector("#close-node-files").addEventListener("click", () => {
    if (canLeaveSource()) nodeFilesDialog.close();
  });
  nodeFilesDialog.addEventListener("cancel", event => {
    if (!canLeaveSource()) event.preventDefault();
  });
  nodeFilesDialog.addEventListener("close", () => {
    if (!nodeFilesDialog.open) {
      nodeFilesRequest?.abort();
      nodeSourceRequest?.abort();
      nodeSourceRequest = null;
      currentSource = null;
      updateSourceDirty();
      releaseNodeFilesLock();
    }
  });

  function nodeFilesBase() {
    return getStopPhase() === "stopped" ? window.parent.phyzflowRecoveryNodeBase?.(window) ?? "" : "";
  }

  async function requestSource(index, data, signal) {
    const headers = { "Content-Type": "application/json" };
    const sessionId = currentEditSessionId();
    if (sessionId) headers["X-Phyzflow-Edit-Session"] = sessionId;
    const response = await fetch(`${nodeFilesBase()}node-source/${index}`, {
      method: "POST",
      headers,
      body: JSON.stringify(data),
      signal,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const fallback = response.status === 409
        ? "The flow was reloaded. Copy any unsaved changes before reopening this file."
        : "Could not access the source file.";
      throw new Error(result.error ?? fallback);
    }
    return result;
  }

  async function openSource(index, path, line = null) {
    if (!canLeaveSource()) return;
    nodeSourceRequest?.abort();
    const request = new AbortController();
    nodeSourceRequest = request;
    nodeSourceInput.readOnly = true;
    saveNodeSource.disabled = true;
    nodeSourceMessage.textContent = "Loading source…";
    try {
      const { source, revision } = await requestSource(index, { path }, request.signal);
      if (request.signal.aborted) return;
      currentSource = { index, path, text: source, revision };
      nodeSourceName.textContent = path;
      nodeSourceInput.value = source;
      nodeSourceForm.hidden = false;
      nodeSourceInput.scrollTop = nodeSourceInput.scrollLeft = 0;
      highlightSource();
      nodeSourceMessage.textContent = revision === "missing" ? "Save to create settings.json." : "";
      if (Number.isInteger(line) && line > 0) {
        const lines = nodeSourceInput.value.split("\n", line);
        if (lines.length === line) {
          const end = lines.join("\n").length;
          const lineHeight = parseFloat(getComputedStyle(nodeSourceInput).lineHeight);
          nodeSourceInput.focus();
          nodeSourceInput.setSelectionRange(end - lines.at(-1).length, end);
          nodeSourceInput.scrollTop = Math.max(0, (line - 1) * lineHeight - nodeSourceInput.clientHeight / 3);
          syncSourceScroll();
          nodeSourceMessage.textContent = `Line ${line} selected.`;
        } else {
          nodeSourceMessage.textContent = `Line ${line} is unavailable in the current file.`;
        }
      }
      for (const button of nodeFilesTree.querySelectorAll("[data-file]")) {
        const selected = button.dataset.file === path;
        button.setAttribute("aria-current", String(selected));
        if (selected) {
          for (let folder = button.closest("details"); folder; folder = folder.parentElement.closest("details")) {
            folder.open = true;
          }
        }
      }
    } catch (error) {
      if (!request.signal.aborted) nodeSourceMessage.textContent = error.message;
    } finally {
      if (nodeSourceRequest === request) {
        nodeSourceRequest = null;
        nodeSourceInput.readOnly = nodeSourceLocked();
        updateSourceDirty();
        const lockMsg = externalEditLockMessage();
        if (lockMsg) nodeSourceMessage.textContent = `Read-only · ${lockMsg}`;
      }
    }
  }

  nodeSourceInput.addEventListener("input", () => {
    updateSourceDirty();
    cancelAnimationFrame(highlightFrame);
    highlightFrame = requestAnimationFrame(highlightSource);
    nodeSourceMessage.textContent = "";
  });
  nodeSourceForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (nodeSourceLocked() || savingSource || nodeSourceRequest || !currentSource ||
        (!sourceIsDirty() && currentSource.revision !== "missing")) return;
    const { index, path, revision } = currentSource;
    const source = nodeSourceInput.value;
    savingSource = true;
    updateSourceDirty();
    nodeSourceInput.readOnly = true;
    nodeSourceMessage.textContent = "Saving…";
    try {
      const saved = await requestSource(index, { path, source, revision });
      currentSource = { index, path, text: source, revision: saved.revision };
      if (editRunningSource && path.endsWith(".py")) {
        nodeSourceMessage.textContent = "Saved. The next run will use this source.";
      } else {
        nodeSourceMessage.textContent = window.parent === window
          ? "Saved. Restart the flow UI to run the edited code."
          : "Saved. Close the editor and select Reload flow to run the edited code.";
      }
    } catch (error) {
      nodeSourceMessage.textContent = error.message;
    } finally {
      savingSource = false;
      nodeSourceInput.readOnly = nodeSourceLocked();
      updateSourceDirty();
    }
  });

  function fileTree(entries, parent, source, index) {
    const list = element("ul", "", "file-tree");
    for (const entry of entries) {
      const path = parent ? `${parent}/${entry.name}` : entry.name;
      const item = element("li", "");
      if (entry.children === null) {
        const editable = sourceLanguage(path) !== null;
        const file = element(editable ? "button" : "code", entry.name, path === source ? "node-entrypoint" : "node-file");
        if (editable) {
          file.type = "button";
          file.dataset.file = path;
          file.addEventListener("click", () => openSource(index, path));
        }
        item.append(file);
      } else {
        const folder = element("details", "");
        folder.append(element("summary", entry.name), fileTree(entry.children, path, source, index));
        if (source?.startsWith(`${path}/`)) folder.open = true;
        item.append(folder);
      }
      list.append(item);
    }
    return list;
  }

  async function showNodeFiles(id, index, source, line = null, preferSettings = false) {
    if (!canLeaveSource()) return;
    source = source?.replaceAll("\\", "/");
    nodeFilesRequest?.abort();
    nodeSourceRequest?.abort();
    nodeSourceRequest = null;
    currentSource = null;
    updateSourceDirty();
    nodeSourceForm.hidden = true;
    nodeSourceMessage.textContent = "Select a Python or JSON file.";
    const request = new AbortController();
    nodeFilesRequest = request;
    nodeFilesHeading.textContent = `${id} files`;
    nodeFilesPath.hidden = true;
    nodeFilesTree.replaceChildren();
    nodeFilesMessage.textContent = "Loading files…";
    if (!nodeFilesDialog.open) nodeFilesDialog.showModal();
    try {
      if (window.parent !== window && window.parent.phyzflowAcquireEditLock) {
        nodeFilesEditLock = await window.parent.phyzflowAcquireEditLock(window);
        if (request.signal.aborted || !nodeFilesDialog.open) {
          releaseNodeFilesLock();
          return;
        }
        if (nodeFilesEditLock.editLock) sheetEditLock = nodeFilesEditLock.editLock;
      }
      const response = await fetch(`${nodeFilesBase()}node-files/${index}`, { signal: request.signal });
      if (!response.ok) throw new Error();
      const { path, entries, truncated, settings } = await response.json();
      if (request.signal.aborted) return;
      if (path === null) {
        nodeFilesMessage.textContent = "No source file was found inside this flow.";
        return;
      }
      nodeFilesPath.textContent = path === "." ? "Flow directory" : path;
      nodeFilesPath.hidden = false;
      nodeFilesTree.replaceChildren(fileTree(entries, path === "." ? "" : path, source, index));
      nodeFilesMessage.textContent = truncated ? "Showing part of this folder tree." : "";
      const files = [...nodeFilesTree.querySelectorAll("[data-file]")].map(button => button.dataset.file);
      const diagnostic = recoveryView ? window.parent.phyzflowRecoveryDiagnostic?.(window) ?? "" : "";
      const errorFile = files.find(file => diagnostic.includes(`/${file}:`) || diagnostic.startsWith(`${file}:`));
      if (errorFile) {
        const location = diagnostic.slice(diagnostic.indexOf(`${errorFile}:`) + errorFile.length + 1);
        await openSource(index, errorFile, Number.parseInt(location, 10));
      } else if (preferSettings && settings) await openSource(index, settings);
      else if (files.includes(source) || line !== null) await openSource(index, source, line);
    } catch (error) {
      if (!request.signal.aborted) nodeFilesMessage.textContent = "Could not load the node files. Close this view and try again.";
    }
  }


  function loadProject(project) {
    editRunningSource = project.edit_running_source;
    sheetEditLock = (window.parent !== window ? window.parent.phyzflowSheetEditLock?.(window) : null) ?? project.edit_lock ?? null;
    syncEditLockUI();
  }

  function syncRunState() {
    if (currentSource && !savingSource && !nodeSourceRequest) {
      nodeSourceInput.readOnly = nodeSourceLocked();
      updateSourceDirty();
    }
  }

  function discardDraft() {
    if (nodeFilesDialog.open) nodeFilesDialog.close();
  }

  return { discardDraft, loadProject, showNodeFiles, syncRunState };
}
