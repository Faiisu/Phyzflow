const graph = document.querySelector("#graph");
const canvas = graph.querySelector("canvas");
const form = document.querySelector("form");
const input = document.querySelector("#input");
const button = form.querySelector("button");
const status = document.querySelector("#status");
const result = document.querySelector("#result");
const nodes = new Map();
const labels = { running: "Running", succeeded: "Succeeded", failed: "Failed", skipped: "Skipped" };

function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function positionGraph(steps) {
  const rows = new Map();
  for (const step of steps) {
    const column = Math.max(0, ...step.dependencies.map(id => nodes.get(id).column)) + 1;
    const row = (rows.get(column) ?? 0) + 1;
    rows.set(column, row);

    const node = element("article", "", "node");
    node.style.gridColumn = column;
    node.style.gridRow = row;
    const location = step.file ? `${step.file.split("/").slice(-2).join("/")}:${step.line}` : "Source unavailable";
    const file = element("p", location);
    file.title = step.file ? `${step.file}:${step.line}` : location;
    const state = element("p", "", "state");
    node.append(element("h3", step.id), file, state);
    if (step.dependencies.length) {
      node.append(element("p", `Depends on ${step.dependencies.join(", ")}`, "sr-only"));
    }
    graph.append(node);
    nodes.set(step.id, { node, column, state });
  }
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
  ctx.strokeStyle = "#517360";
  ctx.fillStyle = "#517360";
  ctx.lineWidth = 1.5;
  for (const step of steps) {
    const downstream = nodes.get(step.id);
    const to = downstream.node.getBoundingClientRect();
    for (const id of step.dependencies) {
      const upstream = nodes.get(id);
      const from = upstream.node.getBoundingClientRect();
      const x1 = from.right - bounds.left;
      const x2 = to.left - bounds.left;
      const y1 = (from.top + from.bottom) / 2 - bounds.top;
      const y2 = (to.top + to.bottom) / 2 - bounds.top;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      if (downstream.column - upstream.column > 1) {
        const bypass = bounds.height - 19;
        ctx.lineTo(x1 + 10, y1);
        ctx.lineTo(x1 + 10, bypass);
        ctx.lineTo(x2 - 10, bypass);
        ctx.lineTo(x2 - 10, y2);
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

form.addEventListener("submit", async event => {
  event.preventDefault();
  result.replaceChildren();
  button.disabled = true;
  setStatus("running");
  for (const { node, state } of nodes.values()) {
    delete node.dataset.state;
    state.textContent = "";
  }
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
  positionGraph(project.steps);
  input.value = JSON.stringify(project.input, null, 2);
  button.disabled = false;
} catch (error) {
  result.replaceChildren();
  showMessage({ error: error.message });
}
