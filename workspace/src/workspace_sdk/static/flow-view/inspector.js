import { blockedByText, element, formatDuration, inputBindings, labels, locationText } from "./display.js";

export function initInspector({ graph, showNodeFiles, openDashboard }) {
  const { nodes, focusGraphNode } = graph;
  const inspectorState = document.querySelector("#inspector-state");
  const inspectorDuration = document.querySelector("#inspector-duration");
  const copySource = document.querySelector("#copy-source");
  const openNodeFiles = document.querySelector("#open-node-files");
  const inspectorTabs = [...document.querySelectorAll(".inspector-tabs [role=tab]")];
  const executionNote = document.querySelector("#execution-note");
  const executionTiming = document.querySelector("#execution-timing");
  const executionStatus = document.querySelector("#execution-status");
  const executionIterationsRow = document.querySelector("#execution-iterations-row");
  const executionIterations = document.querySelector("#execution-iterations");
  const executionStarted = document.querySelector("#execution-started");
  const executionFinished = document.querySelector("#execution-finished");
  const executionDuration = document.querySelector("#execution-duration");
  const executionMetrics = document.querySelector("#execution-metrics");
  const executionMetricsHeading = document.querySelector("#execution-metrics-heading");
  const executionMetricGroups = document.querySelector("#execution-metric-groups");
  const currentNodeTiming = new Map();
  const completedRunStates = ["succeeded", "failed"];
  let currentRunId = null;
  let currentRunFinished = false;

  function selectInspectorTab(selected) {
    for (const tab of inspectorTabs) {
      const active = tab === selected;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      document.getElementById(tab.getAttribute("aria-controls")).hidden = !active;
    }
  }

  for (const [index, tab] of inspectorTabs.entries()) {
    tab.addEventListener("click", () => selectInspectorTab(tab));
    tab.addEventListener("keydown", event => {
      const target = {
        ArrowLeft: inspectorTabs.at(index - 1),
        ArrowRight: inspectorTabs[(index + 1) % inspectorTabs.length],
        Home: inspectorTabs[0],
        End: inspectorTabs.at(-1),
      }[event.key];
      if (!target) return;
      event.preventDefault();
      selectInspectorTab(target);
      target.focus();
    });
  }

  function showExecutionTime(container, timestamp, fallback) {
    if (typeof timestamp !== "string" || !Number.isFinite(Date.parse(timestamp))) {
      container.textContent = fallback;
      return;
    }
    const time = element("time", timestamp.replace("T", " ").replace(/(?:\+00:00|Z)$/, " UTC"));
    time.dateTime = timestamp;
    container.replaceChildren(time);
  }

  function renderInspectorExecution() {
    const timing = currentNodeTiming.get(graph.activeNode);
    executionTiming.hidden = !timing;
    executionNote.hidden = Boolean(timing);
    renderExecutionMetrics(timing);

    if (!timing) {
      executionNote.textContent = currentRunId
        ? "No execution event received for this node."
        : "Run the flow to inspect live execution.";
      return;
    }

    executionStatus.textContent = labels[timing.status] ?? "Unavailable";
    const iterations = timing.iterations;
    executionIterationsRow.hidden = !(iterations > 0);
    if (iterations > 0) executionIterations.textContent = String(iterations);
    const skipped = timing.status === "skipped";
    const running = timing.status === "running";
    showExecutionTime(executionStarted, skipped ? null : timing.started_at, skipped ? "Not run" : "Unavailable");
    // Older saved runs recorded the last event time but omitted the start timestamp.
    const finished = skipped || running ? null : timing.finished_at ?? timing.time;
    showExecutionTime(executionFinished, finished, skipped ? "Not run" : running ? "Running" : "Unavailable");
    executionDuration.textContent = skipped ? "Not measured" : formatDuration(timing.duration_ms) || (running ? "Running" : "Unavailable");
  }

  function renderExecutionMetrics(timing) {
    const live = timing?.status === "running";
    const metrics = live ? timing.liveMetrics : currentRunFinished ? timing?.metrics : null;
    executionMetricsHeading.textContent = live ? "Live progress and metrics" : "Operational metrics";
    const groups = Object.entries(metrics ?? {}).map(([name, values], index) => {
      const group = element("div", "", "execution-metric-group");
      const title = element("h4", name);
      title.id = `execution-metric-group-${index}`;
      group.setAttribute("role", "group");
      group.setAttribute("aria-labelledby", title.id);
      const list = element("dl");
      for (const [label, value] of Object.entries(values)) {
        const row = element("div");
        row.append(element("dt", label), element("dd", String(value)));
        list.append(row);
      }
      group.append(title, list);
      return group;
    });
    executionMetrics.hidden = groups.length === 0;
    executionMetricGroups.replaceChildren(...groups);
  }

  function trackInspectorExecution(message) {
    if (!message.run_id || !message.status) return;
    if (!message.step_id) {
      if (message.status === "running") {
        currentRunId = message.run_id;
      }
      currentRunFinished = completedRunStates.includes(message.status);
      renderInspectorExecution();
      return;
    }

    if (message.live_metrics) {
      const timing = currentNodeTiming.get(message.step_id);
      if (message.run_id !== currentRunId || timing?.status !== "running") return;
      const groups = { ...timing.liveMetrics, ...message.live_metrics };
      timing.liveMetrics = Object.fromEntries(Object.entries(groups).slice(-8));
      if (graph.activeNode === message.step_id) renderExecutionMetrics(timing);
      return;
    }

    const timing = currentNodeTiming.get(message.step_id) ?? {};
    timing.status = message.status;
    if (message.iteration != null) {
      timing.iterations = Math.max(timing.iterations ?? 0, message.iteration);
    }
    if (message.status === "running") {
      timing.started_at ??= message.time;
      delete timing.liveMetrics;
    } else if (message.status !== "skipped") {
      timing.finished_at = message.time;
      timing.duration_ms = message.duration_ms;
      timing.metrics = message.metrics;
    }
    currentNodeTiming.set(message.step_id, timing);
  }

  function showInspectorState(value, duration = "") {
    inspectorState.textContent = labels[value] ?? "Idle";
    inspectorState.dataset.state = value ?? "idle";
    inspectorDuration.textContent = duration;
    inspectorDuration.hidden = !duration;
    const skipped = document.querySelector("#inspector-blocked-by");
    const blockers = nodes.get(graph.activeNode)?.blockedBy ?? [];
    skipped.textContent = blockers.length ? blockedByText(blockers, id => nodes.get(id)?.node.dataset.state) : "";
    skipped.hidden = !blockers.length;
    renderInspectorExecution();
  }

  function dependencyButton(id) {
    const button = element("button", id, "inspector-dependency");
    button.type = "button";
    button.addEventListener("click", () => focusGraphNode(id));
    return button;
  }

  function inspectNode(step, project) {
    const { node, index, duration } = nodes.get(step.id);
    openNodeFiles.onclick = () => showNodeFiles(step.id, index, step.source);
    document.querySelector("#visualize-node").onclick = () => openDashboard(step.id);
    document.querySelector("#inspector-empty").hidden = true;
    document.querySelector("#inspector-detail").hidden = false;
    document.querySelector("#inspector-name").textContent = step.id;
    showInspectorState(node.dataset.state, duration.textContent);

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
      if (graph.activeNode === step.id) copySource.textContent = feedback;
    };

    const inputs = step.arguments.map(argument => {
      const item = element("li", argument.name);
      if (argument.kind === "loop") {
        const suffix = argument.max_iterations ? `Loop · max ${argument.max_iterations}` : "Loop";
        item.append(dependencyButton(argument.source), element("small", suffix, "edge-loop-tag"));
      } else if (argument.kind === "node") {
        item.append(dependencyButton(argument.source));
      } else {
        item.append(element("small", `Run input · ${argument.source}`));
      }
      return item;
    });
    const feeds = [];
    for (const next of project.steps) {
      if (next.dependencies.includes(step.id)) {
        const item = element("li", "");
        item.append(dependencyButton(next.id), element("small", inputBindings(next, step.id, "node").join(", ")));
        feeds.push(item);
      }
      if ((next.loop_dependencies ?? []).includes(step.id)) {
        const item = element("li", "");
        const bindings = inputBindings(next, step.id, "loop").join(", ");
        item.append(dependencyButton(next.id), element("small", `${bindings} · Loop`, "edge-loop-tag"));
        feeds.push(item);
      }
    }
    if (step.id === project.output) feeds.push(element("li", "Run output"));
    document.querySelector("#inspector-inputs").replaceChildren(...(inputs.length ? inputs : [element("li", "None")]));
    document.querySelector("#inspector-feeds").replaceChildren(...(feeds.length ? feeds : [element("li", "None")]));
  }

  function resetTiming() {
    currentRunId = null;
    currentRunFinished = false;
    currentNodeTiming.clear();
  }

  function stopTiming(id) {
    const timing = currentNodeTiming.get(id);
    if (timing) timing.status = "stopped";
  }

  return {
    inspectNode, resetTiming, showInspectorState, stopTiming, trackInspectorExecution,
    get currentRunId() { return currentRunId; },
  };
}
