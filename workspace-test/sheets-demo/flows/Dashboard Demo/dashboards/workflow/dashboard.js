const status = document.querySelector("#status");
const list = document.querySelector("#steps");

workspaceDashboard.subscribe(({ graph, run }) => {
  status.textContent = run ? `Run ${run.id.slice(0, 8)} · ${run.status}` : "Run the flow to collect a batch.";
  list.replaceChildren(...graph.map(node => {
    const step = run?.steps.find(item => item.id === node.id);
    const row = document.createElement("li");
    const label = document.createElement(node.id === "Collect readings" ? "button" : "span");
    label.textContent = node.id;
    if (label instanceof HTMLButtonElement) {
      label.type = "button";
      label.onclick = () => workspaceDashboard.openNode(node.id);
    }
    const measurement = document.createElement("span");
    const duration = step?.duration_ms;
    measurement.textContent = duration === undefined
      ? step?.status ?? "Waiting"
      : `${step.status} · ${duration.toFixed(1)} ms`;
    row.append(label, measurement);
    return row;
  }));
});
