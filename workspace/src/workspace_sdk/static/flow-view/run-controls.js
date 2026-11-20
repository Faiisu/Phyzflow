import { blockedByText, element, formatDuration, jsonOutput, labels, locationText } from "./display.js";

export function initRunControls({
  graph, inspector, editor, getDashboards, setPanel, onRunChange, recoveryView,
}) {
  const { nodes, focusGraphNode } = graph;
  const { showInspectorState, trackInspectorExecution } = inspector;
  const form = document.querySelector("#run-form");
  const input = document.querySelector("#input");
  const button = document.querySelector("#run-flow");
  const runInput = document.querySelector("#run-input");
  const resetInput = document.querySelector("#reset-input");
  const status = document.querySelector("#status");
  const result = document.querySelector("#result");
  const consoleStreams = {
    stdout: document.querySelector("#console-stdout"),
    stderr: document.querySelector("#console-stderr"),
  };
  const saveConsoleLogs = document.querySelector("#save-console-logs");
  const consoleRetentionDays = document.querySelector("#console-retention-days");
  const consoleSettingsMessage = document.querySelector("#console-settings-message");
  let consoleRunId = null;
  let stopPhase = null;
  let runInProgress = false;
  let streamingOwnRun = false;
  let projectReady = false;
  let runStatus = null;
  let deferredRunError = null;
  let liveOutput = null;

  function setStatus(value, label = labels[value]) {
    runStatus = { state: value, label };
    status.textContent = label;
    status.dataset.state = value;
    if (onRunChange) onRunChange(window);
  }

  function clearConsole() {
    for (const pane of Object.values(consoleStreams)) pane.textContent = "";
  }

  function showMessage(message) {
    if (stopPhase === "stopped") return;
    if (message.run_id && message.run_id !== consoleRunId) {
      consoleRunId = message.run_id;
      clearConsole();
    }
    if (message.kind === "console") {
      const pane = consoleStreams[message.stream];
      const follow = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 15;
      const text = pane.textContent + message.text;
      pane.textContent = text.slice(-65536);
      if (follow) pane.scrollTop = pane.scrollHeight;
      return;
    }
    if (message.live_frame || message.setting_change) return;
    if (message.output_preview !== undefined) {
      if (!liveOutput) {
        liveOutput = element("section");
        liveOutput.setAttribute("aria-label", "Live output");
        liveOutput.append(element("h3", "Live output"));
        result.append(liveOutput);
      }
      liveOutput.querySelector("pre")?.remove();
      liveOutput.append(jsonOutput(message.output_preview));
      return;
    }
    trackInspectorExecution(message);
    if (message.live_metrics) return;
    if (message.history_error || message.console_log_error) {
      result.append(element("p", message.history_error ?? message.console_log_error, "error"));
      return;
    }
    if (message.error) {
      setStatus("failed");
      result.append(element("p", message.error, "error"));
      return;
    }
    if (message.result) {
      setStatus(message.result.status);
      if (message.result.status === "succeeded") {
        result.append(element("h3", "Output"), jsonOutput(JSON.stringify(message.result.output, null, 2)));
      }
      return;
    }
    if (!message.step_id) {
      setStatus(message.status);
      return;
    }
    const entry = nodes.get(message.step_id);
    const { node, state, duration, index } = entry;
    entry.blockedBy = message.blocked_by;
    node.dataset.state = message.status;
    state.textContent = labels[message.status];
    duration.textContent = formatDuration(message.duration_ms);
    duration.hidden = !duration.textContent;
    if (graph.activeNode === message.step_id) showInspectorState(message.status, duration.textContent);
    if (message.status === "failed") {
      const error = element("article", "", "error");
      const actions = element("div", "", "error-actions");
      const focus = element("button", "Focus node", "text-button");
      focus.type = "button";
      focus.addEventListener("click", () => focusGraphNode(message.step_id));
      actions.append(focus);
      if (message.source_target) {
        const source = element("button", "Open source", "text-button open-error-source");
        source.type = "button";
        source.addEventListener("click", () => editor.showNodeFiles(message.step_id, index, message.source_target, message.line));
        actions.append(source);
      }
      const trace = document.createElement("details");
      trace.open = true;
      trace.append(element("summary", "Diagnostics"), element("pre",
        message.traceback ?? "See saved run history for diagnostic locations."));
      error.append(
        element("h3", `${message.step_id} failed`),
        element("strong", message.error_type),
        element("p", locationText(message)),
        actions,
        trace,
      );
      if (message.blocked_by?.length) {
        error.append(element("p", blockedByText(message.blocked_by, id => nodes.get(id)?.node.dataset.state)));
      }
      result.append(error);
    }
  }

  function revealRunPanel() {
    setPanel("run", true).scrollIntoView({ block: "nearest" });
  }

  button?.addEventListener("click", revealRunPanel);
  const canRun = () => projectReady && !runInProgress && !stopPhase;
  if (onRunChange && !recoveryView) {
    window.phyzflowRunControls = {
      get snapshot() {
        return { status: runStatus, canRun: canRun() };
      },
      run() {
        if (!canRun()) return;
        revealRunPanel();
        form.requestSubmit(runInput);
      },
    };
  }

  function updateRunControls() {
    runInput.disabled = !canRun();
    if (button) button.disabled = runInput.disabled;
    if (onRunChange) onRunChange(window);
    editor.syncRunState();
  }

  function showRunStreamError(message) {
    getDashboards().track({ error: message });
    setStatus("failed");
    result.append(element("p", message, "error run-stream-error"));
  }

  window.addEventListener("workspace-stop", event => {
    const { phase, discardUnsaved } = event.detail;
    stopPhase = phase;
    if (phase === "stopped") runInProgress = false;
    updateRunControls();
    if (phase === "stopped") {
      setStatus("stopped", "Stopped");
      for (const [id, { node, state }] of nodes) {
        if (node.dataset.state !== "running") continue;
        node.dataset.state = "stopped";
        state.textContent = labels.stopped;
        inspector.stopTiming(id);
        if (graph.activeNode === id) showInspectorState("stopped");
      }
      result.querySelector(".run-stream-error")?.remove();
      resetInput.disabled = saveConsoleLogs.disabled = consoleRetentionDays.disabled = true;
      for (const control of document.querySelectorAll(".open-error-source")) control.disabled = true;
      if (discardUnsaved) editor.discardDraft();
    } else if (!phase && deferredRunError) {
      showRunStreamError(deferredRunError);
    }
    if (phase !== "stopping") deferredRunError = null;
  });

  function resetRunResult() {
    result.replaceChildren();
    liveOutput = null;
    inspector.resetTiming();
    for (const entry of nodes.values()) {
      const { node, state, duration } = entry;
      entry.blockedBy = undefined;
      delete node.dataset.state;
      state.textContent = "Idle";
      duration.textContent = "";
      duration.hidden = true;
    }
    showInspectorState();
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!canRun()) return;
    runInProgress = true;
    streamingOwnRun = true;
    consoleRunId = null;
    clearConsole();
    updateRunControls();
    setPanel("run", true);
    resetRunResult();
    setStatus("running");
    try {
      const payload = JSON.parse(input.value);
      if (payload === null || Array.isArray(payload) || typeof payload !== "object") {
        throw new Error("Enter a JSON object, then run the flow again.");
      }
      const response = await fetch("run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(`The flow could not start (${response.status}).`);
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      let receivedResult = false;
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += chunk.value;
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          const message = JSON.parse(line);
          getDashboards().track(message);
          showMessage(message);
          if (message.result || message.error) receivedResult = true;
        }
      }
      if (!receivedResult) throw new Error("The run ended before returning a result.");
    } catch (error) {
      if (stopPhase === "stopping") deferredRunError = error.message;
      else if (!stopPhase) showRunStreamError(error.message);
    } finally {
      streamingOwnRun = false;
      runInProgress = false;
      updateRunControls();
    }
  });

  async function reconnectRun() {
    let cursor = 0;
    let observedRun = null;
    while (!stopPhase) {
      try {
        if (streamingOwnRun) {
          await new Promise(resolve => setTimeout(resolve, 250));
          continue;
        }
        const response = await fetch("live-run");
        if (response.status === 503 || response.status === 404 || response.status === 409) break;
        if (!response.ok) throw new Error("Could not reconnect to the running flow.");
        const snapshot = await response.json();
        if (stopPhase === "stopped") return;
        // A POST stream already rendered this run; only attach to a different owner.
        const runId = snapshot.events.find(([, message]) => message.run_id)?.[1].run_id;
        const consoleOnly = snapshot.events.length > 0 && snapshot.events.every(([, item]) => item.kind === "console");
        if (runId && runId !== observedRun) {
          const hadObserved = observedRun !== null;
          observedRun = runId;
          if (runId === inspector.currentRunId || (runId === consoleRunId && consoleOnly)) {
            cursor = Infinity;
          } else {
            cursor = 0;
            if (hadObserved || !consoleOnly) resetRunResult();
          }
        }
        runInProgress = snapshot.running;
        updateRunControls();
        for (const [sequence, message] of snapshot.events) {
          if (sequence <= cursor) continue;
          if (message.step_id && !nodes.has(message.step_id)) continue;
          getDashboards().track(message);
          showMessage(message);
          cursor = sequence;
        }
        if (snapshot.status === "stopped") setStatus("stopped");
      } catch (error) {
        // A temporary disconnect must not mark the server-owned run as failed.
      }
      await new Promise(resolve => setTimeout(resolve, 250));
    }
  }

  let consoleSettingsSequence = 0;
  let savedConsoleSettings = { save_logs: true, retention_days: 7 };
  let retentionSaveTimer = null;

  function applyConsoleSettings(settings) {
    if (!settings) return;
    savedConsoleSettings = {
      save_logs: Boolean(settings.save_logs),
      retention_days: Number(settings.retention_days),
    };
    saveConsoleLogs.checked = savedConsoleSettings.save_logs;
    consoleRetentionDays.value = String(savedConsoleSettings.retention_days);
  }

  function showConsoleSettingsStatus(text, isError = false) {
    consoleSettingsMessage.textContent = text;
    consoleSettingsMessage.classList.toggle("error", isError);
    consoleSettingsMessage.hidden = !text;
  }

  function validRetentionDays() {
    const rawRetention = consoleRetentionDays.value.trim();
    const retentionDays = Number(rawRetention);
    if (!/^[0-9]+$/.test(rawRetention) || retentionDays < 1 || retentionDays > 3650) {
      return null;
    }
    return retentionDays;
  }

  async function saveConsoleLogSettings() {
    clearTimeout(retentionSaveTimer);
    retentionSaveTimer = null;
    if (stopPhase === "stopped") return;
    const retentionDays = validRetentionDays();
    if (retentionDays === null) {
      showConsoleSettingsStatus("Enter a whole number from 1 to 3650 days.", true);
      return;
    }
    const saveLogs = saveConsoleLogs.checked;
    if (saveLogs === savedConsoleSettings.save_logs && retentionDays === savedConsoleSettings.retention_days) {
      return;
    }
    const requestId = ++consoleSettingsSequence;
    showConsoleSettingsStatus("Saving…");
    try {
      const response = await fetch("console-logs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          save_logs: saveLogs,
          retention_days: retentionDays,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error ?? "Could not save console log settings.");
      if (requestId !== consoleSettingsSequence) return;
      applyConsoleSettings(data.settings);
      showConsoleSettingsStatus("Saved.");
    } catch (error) {
      if (requestId !== consoleSettingsSequence) return;
      showConsoleSettingsStatus(error.message, true);
    }
  }

  saveConsoleLogs.addEventListener("change", saveConsoleLogSettings);
  consoleRetentionDays.addEventListener("change", saveConsoleLogSettings);
  consoleRetentionDays.addEventListener("input", () => {
    clearTimeout(retentionSaveTimer);
    if (validRetentionDays() === null) {
      showConsoleSettingsStatus("Enter a whole number from 1 to 3650 days.", true);
      return;
    }
    retentionSaveTimer = setTimeout(saveConsoleLogSettings, 150);
  });
  if (recoveryView) saveConsoleLogs.disabled = consoleRetentionDays.disabled = true;

  function loadConsole(consoleData) {
    if (consoleData?.settings) applyConsoleSettings(consoleData.settings);
    if (!consoleRunId && !runInProgress && consoleData?.events?.length) {
      clearConsole();
      for (const entry of consoleData.events) showMessage(entry);
    }
  }

  function loadSample(inputData) {
    const sample = JSON.stringify(inputData, null, 2);
    input.value = sample;
    input.disabled = false;
    resetInput.addEventListener("click", () => {
      input.value = sample;
      input.focus();
    });
    resetInput.disabled = stopPhase === "stopped";
    projectReady = true;
    updateRunControls();
    reconnectRun();
  }

  function showLoadError(error) {
    setPanel("run", true);
    result.replaceChildren();
    showMessage({ error: error.message });
  }

  return {
    loadConsole, loadSample, showLoadError,
    get stopPhase() { return stopPhase; },
    get runInProgress() { return runInProgress; },
  };
}
