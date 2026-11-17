const pipeline = "Run Parallel Pipeline";
const state = document.querySelector("#run-state");
const timings = document.querySelector("#timings");

workspaceDashboard.subscribe(({ run, graph }) => {
  state.textContent = run ? `${run.status} · ${run.id.slice(0, 8)}` : "Run the flow to compare node timings.";
  const steps = new Map(run?.steps.map(step => [step.id, step]));
  const maximum = Math.max(1, ...[...steps.values()].map(step => step.duration_ms ?? 0));
  timings.replaceChildren(...graph.map(({ id }) => {
    const step = steps.get(id);
    const row = document.createElement("li");
    const name = document.createElement(id === pipeline ? "button" : "span");
    name.textContent = id;
    if (id === pipeline) {
      name.type = "button";
      name.onclick = () => workspaceDashboard.openNode(id);
    }
    const bar = document.createElement("meter");
    bar.setAttribute("aria-label", `${id} duration`);
    bar.max = maximum;
    bar.value = step?.duration_ms ?? 0;
    const value = document.createElement("small");
    value.textContent = step?.duration_ms === undefined
      ? step?.status ?? "Waiting"
      : `${step.duration_ms.toFixed(1)} ms · ${step.status}`;
    row.append(name, bar, value);
    return row;
  }));
});
