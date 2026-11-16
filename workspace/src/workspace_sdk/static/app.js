const graph = document.querySelector("#graph");
const canvas = graph.querySelector("canvas");
const narrowGraph = window.matchMedia("(max-width: 600px)");
const form = document.querySelector("#run-form");
const input = document.querySelector("#input");
const button = document.querySelector("#run-flow");
const resetInput = document.querySelector("#reset-input");
const status = document.querySelector("#status");
const result = document.querySelector("#result");
const outline = document.querySelector("#outline-list");
const tablist = document.querySelector(".dock-tabs");
const tabs = [...tablist.querySelectorAll('[role="tab"]')];
const inspectorState = document.querySelector("#inspector-state");
const copySource = document.querySelector("#copy-source");
const historyList = document.querySelector("#history-list");
const historyDetail = document.querySelector("#history-detail");
const refreshHistoryButton = document.querySelector("#refresh-history");
const nodes = new Map();
const labels = { running: "Running", succeeded: "Succeeded", failed: "Failed", skipped: "Skipped" };
let activeNode = null;

function showPanel(name) {
  for (const tab of tabs) {
    const active = tab.id === `tab-${name}`;
    tab.tabIndex = active ? 0 : -1;
    tab.setAttribute("aria-selected", String(active));
    document.getElementById(tab.getAttribute("aria-controls")).hidden = !active;
  }
}

for (const tab of tabs) {
  tab.addEventListener("click", () => showPanel(tab.id.slice(4)));
}

tablist.addEventListener("keydown", event => {
  const index = tabs.indexOf(event.target);
  let next;
  if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
  else if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = tabs.length - 1;
  else return;
  event.preventDefault();
  tabs[next].click();
  tabs[next].focus();
});

function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function showInspectorState(value) {
  inspectorState.textContent = labels[value] ?? "Idle";
  inspectorState.dataset.state = value ?? "idle";
}

function inspectNode(step, project) {
  if (activeNode !== null) {
    const previous = nodes.get(activeNode);
    previous.node.classList.remove("is-selected");
    previous.select.setAttribute("aria-pressed", "false");
    previous.outlineSelect.setAttribute("aria-pressed", "false");
  }
  activeNode = step.id;
  const { node, select, outlineSelect } = nodes.get(step.id);
  node.classList.add("is-selected");
  select.setAttribute("aria-pressed", "true");
  outlineSelect.setAttribute("aria-pressed", "true");
  showPanel("inspector");
  document.querySelector("#inspector-empty").hidden = true;
  document.querySelector("#inspector-detail").hidden = false;
  document.querySelector("#inspector-name").textContent = step.id;
  showInspectorState(node.dataset.state);

  const description = document.querySelector("#inspector-description");
  description.textContent = step.description;
  description.hidden = !step.description;
  const source = document.querySelector("#inspector-source");
  source.textContent = locationText({ file: step.source, line: step.line });
  source.title = locationText(step);
  copySource.hidden = !step.file;
  copySource.textContent = "Copy location";
  copySource.onclick = async () => {
    let feedback = "Location copied";
    try {
      await navigator.clipboard.writeText(locationText(step));
    } catch (error) {
      feedback = "Copy failed";
    }
    if (activeNode === step.id) copySource.textContent = feedback;
  };

  const inputs = step.arguments.map(argument => {
    const item = element("li", argument.name);
    const source = argument.kind === "node" ? argument.source : `Run input · ${argument.source}`;
    item.append(element("small", source));
    return item;
  });
  const feeds = project.steps.filter(next => next.dependencies.includes(step.id)).map(next => element("li", next.id));
  if (step.id === project.output) feeds.push(element("li", "Run output"));
  document.querySelector("#inspector-inputs").replaceChildren(...(inputs.length ? inputs : [element("li", "None")]));
  document.querySelector("#inspector-feeds").replaceChildren(...(feeds.length ? feeds : [element("li", "None")]));
  drawEdges(project.steps);
}

function positionGraph(project) {
  const { steps } = project;
  const rows = new Map();
  const producers = new Set(steps.flatMap(step => step.dependencies));
  for (const [index, step] of steps.entries()) {
    const column = Math.max(0, ...step.dependencies.map(id => nodes.get(id).column)) + 1;
    const row = (rows.get(column) ?? 0) + 1;
    rows.set(column, row);

    const node = element("article", "", "node");
    node.style.setProperty("--level", column);
    node.style.setProperty("--lane", row);
    node.classList.toggle("has-input", step.dependencies.length > 0);
    node.classList.toggle("has-output", producers.has(step.id));
    const source = step.source?.split(/[/\\]/).at(-1);
    const file = element("p", locationText({ file: source, line: step.line }), "node-source");
    file.title = locationText(step);
    const state = element("p", "Idle", "state badge");
    const number = String(index + 1).padStart(2, "0");
    const select = element("button", "", "inspect-node");
    select.type = "button";
    select.setAttribute("aria-label", `Inspect ${step.id}`);
    select.setAttribute("aria-pressed", "false");
    select.addEventListener("click", () => inspectNode(step, project));
    const outlineSelect = element("button", "", "outline-node");
    outlineSelect.type = "button";
    outlineSelect.setAttribute("aria-pressed", "false");
    outlineSelect.append(element("span", number, "node-index"), element("span", step.id));
    outlineSelect.addEventListener("click", () => {
      inspectNode(step, project);
      node.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
    const listItem = element("li", "");
    listItem.append(outlineSelect);
    outline.append(listItem);
    node.append(
      element("p", number, "node-index"),
      element("h3", step.id),
      file,
      state,
      select,
    );
    if (step.dependencies.length) {
      node.append(element("p", `Depends on ${step.dependencies.join(", ")}`, "sr-only"));
    }
    graph.append(node);
    nodes.set(step.id, { node, column, state, select, outlineSelect });
  }
  const count = steps.reduce((total, step) => total + step.dependencies.length, 0);
  document.querySelector("#graph-summary").textContent =
    `${steps.length} ${steps.length === 1 ? "node" : "nodes"} / ${count} ${count === 1 ? "edge" : "edges"}`;
  if (steps.length) inspectNode(steps[0], project);
  new ResizeObserver(() => drawEdges(steps)).observe(graph);
}

function drawEdges(steps) {
  const bounds = graph.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  canvas.style.width = `${bounds.width}px`;
  canvas.style.height = `${bounds.height}px`;
  canvas.width = Math.ceil(bounds.width * scale);
  canvas.height = Math.ceil(bounds.height * scale);
  const ctx = canvas.getContext("2d");
  ctx.scale(scale, scale);
  const vertical = narrowGraph.matches;
  if (vertical) ctx.transform(0, 1, 1, 0, 0, 0);
  ctx.lineWidth = 1.6;
  ctx.lineJoin = "round";
  const frames = new Map(steps.map(step => [step.id, nodes.get(step.id).node.getBoundingClientRect()]));
  const bypass = Math.max(0, ...Array.from(frames.values(), frame =>
    vertical ? frame.right - bounds.left : frame.bottom - bounds.top
  )) + 32;
  for (const step of steps) {
    const downstream = nodes.get(step.id);
    const to = frames.get(step.id);
    for (const id of step.dependencies) {
      const upstream = nodes.get(id);
      const from = frames.get(id);
      const x1 = (vertical ? from.bottom - bounds.top : from.right - bounds.left) + 3;
      const x2 = (vertical ? to.top - bounds.top : to.left - bounds.left) - 4;
      const y1 = vertical ? from.left + from.width / 2 - bounds.left : from.top + from.height / 2 - bounds.top;
      const y2 = vertical ? to.left + to.width / 2 - bounds.left : to.top + to.height / 2 - bounds.top;
      const color = activeNode === step.id || activeNode === id ? "#ff9b88" : "#838896";
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      if (downstream.column - upstream.column > 1) {
        ctx.lineTo(x1 + 11, y1);
        ctx.lineTo(x1 + 11, bypass);
        ctx.lineTo(x2 - 11, bypass);
        ctx.lineTo(x2 - 11, y2);
        ctx.lineTo(x2, y2);
      } else {
        const midpoint = (x1 + x2) / 2;
        ctx.bezierCurveTo(midpoint, y1, midpoint, y2, x2, y2);
      }
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - 6, y2 - 4);
      ctx.lineTo(x2 - 6, y2 + 4);
      ctx.fill();
    }
  }
}

function setStatus(value, label = labels[value]) {
  status.textContent = label;
  status.dataset.state = value;
}

function showMessage(message) {
  if (message.history_error) {
    result.append(element("p", message.history_error, "error"));
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
      result.append(element("h3", "Output"), element("pre", JSON.stringify(message.result.output, null, 2)));
    }
    return;
  }
  if (!message.step_id) {
    setStatus(message.status);
    return;
  }
  const { node, state } = nodes.get(message.step_id);
  node.dataset.state = message.status;
  state.textContent = labels[message.status];
  if (activeNode === message.step_id) showInspectorState(message.status);
  if (message.status === "failed") {
    const error = element("article", "", "error");
    const trace = document.createElement("details");
    trace.open = true;
    trace.append(element("summary", "Traceback"), element("pre", message.traceback));
    error.append(
      element("h3", `${message.step_id} failed`),
      element("strong", message.error_type),
      element("p", `${message.file}:${message.line}`),
      trace,
    );
    result.append(error);
  }
}

function locationText(location) {
  if (!location.file) return "Source unavailable";
  return location.line == null ? location.file : `${location.file}:${location.line}`;
}

function showMetadata(container, label, metadata) {
  if (metadata != null) {
    container.append(element("p", label), element("pre", JSON.stringify(metadata, null, 2)));
  }
}

function showSample(container, label, sample) {
  if (sample !== undefined) {
    container.append(element("p", label));
    if (sample === null) {
      container.append(element("p", "No JSON sample available."));
    } else {
      container.append(element("pre", sample));
    }
  }
}

let historySelection = 0;
let selectedRunId = null;
async function showHistoryRun(id) {
  const selection = ++historySelection;
  selectedRunId = id;
  for (const button of historyList.querySelectorAll(".history-run")) {
    button.setAttribute("aria-current", String(button.dataset.runId === id));
  }
  historyDetail.replaceChildren(element("p", "Loading run…"));
  try {
    const response = await fetch(`/history/${id}`);
    if (!response.ok) throw new Error("Could not load this run.");
    const run = await response.json();
    if (selection !== historySelection) return;
    historyDetail.replaceChildren(
      element("h3", "Saved run"),
      element("p", run.id, "run-id"),
      element("p", "Source version"),
      element("code", run.source_version, "source-version"),
      element("p", `Duration ${run.duration_ms.toFixed(2)} ms`),
    );
    showMetadata(historyDetail, "Run input", run.input);
    showMetadata(historyDetail, "Run output", run.output);
    for (const step of run.steps) {
      const node = element("article", "", "history-node");
      node.append(
        element("h4", `${step.id} · ${labels[step.status]}`),
        element("p", locationText(step.source)),
      );
      showMetadata(node, "Input", step.input);
      showMetadata(node, "Output", step.output);
      if (step.reasons?.length) {
        const reasons = element("ul", "");
        for (const reason of step.reasons) reasons.append(element("li", reason));
        node.append(element("p", "Decision reasons"), reasons);
      }
      showSample(node, "Input sample", step.input_sample);
      showSample(node, "Output sample", step.output_sample);
      if (step.error_type) {
        const stack = element("ul", "", "stack-locations");
        for (const frame of step.error_frames) stack.append(element("li", locationText(frame)));
        node.append(element("strong", step.error_type), stack);
      }
      historyDetail.append(node);
    }
  } catch (error) {
    if (selection === historySelection) {
      historyDetail.replaceChildren(element("p", error.message, "error"));
    }
  }
}

async function refreshHistory() {
  refreshHistoryButton.disabled = true;
  try {
    const response = await fetch("/history");
    if (!response.ok) throw new Error("Could not load run history.");
    const runs = await response.json();
    if (!runs.length) {
      historyList.replaceChildren(element("p", "No saved runs yet.", "muted"));
      return;
    }
    const list = element("ul", "");
    for (const run of runs) {
      const item = element("li", "");
      const select = element("button", "", "history-run");
      select.type = "button";
      select.dataset.runId = run.id;
      select.setAttribute("aria-current", String(run.id === selectedRunId));
      const state = element("strong", labels[run.status], "badge");
      state.dataset.state = run.status;
      const date = element("time", new Date(run.started_at).toLocaleString());
      date.dateTime = run.started_at;
      select.append(state, date);
      select.addEventListener("click", () => showHistoryRun(run.id));
      item.append(select);
      list.append(item);
    }
    historyList.replaceChildren(list);
  } catch (error) {
    historyList.replaceChildren(element("p", error.message, "error"));
  } finally {
    refreshHistoryButton.disabled = false;
  }
}

refreshHistoryButton.addEventListener("click", refreshHistory);
refreshHistory();
button.addEventListener("click", () => showPanel("run"));

form.addEventListener("submit", async event => {
  event.preventDefault();
  showPanel("run");
  result.replaceChildren();
  button.disabled = true;
  setStatus("running");
  for (const { node, state } of nodes.values()) {
    delete node.dataset.state;
    state.textContent = "Idle";
  }
  showInspectorState();
  try {
    const payload = JSON.parse(input.value);
    if (payload === null || Array.isArray(payload) || typeof payload !== "object") {
      throw new Error("Enter a JSON object, then run the flow again.");
    }
    const response = await fetch("/run", {
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
        showMessage(message);
        if (message.result || message.error) receivedResult = true;
      }
    }
    if (!receivedResult) throw new Error("The run ended before returning a result.");
  } catch (error) {
    showMessage({ error: error.message });
  } finally {
    button.disabled = false;
  }
});

try {
  const response = await fetch("/project");
  if (!response.ok) throw new Error("Could not load the project graph. Restart Workspace and try again.");
  const project = await response.json();
  const name = document.querySelector("#project-name");
  name.textContent = project.name;
  name.title = project.name;
  name.hidden = false;
  positionGraph(project);
  const sample = JSON.stringify(project.input, null, 2);
  input.value = sample;
  resetInput.addEventListener("click", () => {
    input.value = sample;
    input.focus();
  });
  resetInput.disabled = false;
  button.disabled = false;
} catch (error) {
  showPanel("run");
  result.replaceChildren();
  showMessage({ error: error.message });
}
