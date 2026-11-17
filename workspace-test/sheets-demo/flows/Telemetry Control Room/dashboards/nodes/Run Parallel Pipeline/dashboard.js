const node = "Run Parallel Pipeline";
const name = "Submissions per worker";
const state = document.querySelector("#pipeline-state");
const metrics = document.querySelector("#pipeline-metrics");
const progress = document.querySelector("#progress");
const prepared = document.querySelector("#prepared");
const preparedLabel = document.querySelector("#prepared-label");
const form = document.querySelector("#setting");
const input = document.querySelector("#submissions");
const live = document.querySelector("#apply-live");
const feedback = document.querySelector("#setting-result");
let savedValue;
let pendingId;
let changes = [];

function showChange(change) {
  if (!change) return;
  switch (change.status) {
    case "queued":
      feedback.textContent = "Waiting for the node to apply the change…";
      break;
    case "applied":
      feedback.textContent = `Applied ${change.value} at ${new Date(change.time).toLocaleTimeString()} in run ${change.runId.slice(0, 8)}.`;
      break;
    case "saved":
      feedback.textContent = `Saved ${change.value} for the next run.`;
      break;
    case "rejected":
      feedback.textContent = change.message;
  }
}

workspaceDashboard.subscribe(snapshot => {
  const step = snapshot.run?.steps.find(step => step.id === node);
  state.textContent = step
    ? `${step.status}${step.duration_ms === undefined ? "" : ` · ${step.duration_ms.toFixed(1)} ms`}`
    : "Run the flow to inspect this node.";
  const preview = snapshot.previews[node];
  progress.hidden = !preview;
  if (preview) {
    prepared.max = preview.total;
    prepared.value = preview.prepared;
    preparedLabel.textContent = `${preview.prepared} / ${preview.total} prepared`;
  }
  const groups = step?.status === "running" ? snapshot.liveMetrics[node] : step?.metrics;
  metrics.replaceChildren(...Object.entries(groups ?? {}).flatMap(([group, values]) =>
    Object.entries(values).flatMap(([key, value]) => {
      const label = document.createElement("dt");
      label.textContent = `${group} · ${key}`;
      const reading = document.createElement("dd");
      reading.textContent = value;
      return [label, reading];
    }),
  ));
  const setting = snapshot.settings[node]?.[name];
  form.hidden = !setting;
  if (setting) {
    input.min = setting.minimum;
    input.max = setting.maximum;
    if (savedValue !== setting.value) {
      savedValue = setting.value;
      input.value = savedValue;
    }
  }
  live.disabled = !setting?.live || snapshot.mode !== "live" || step?.status !== "running";
  changes = snapshot.settingChanges;
  const change = changes.find(change => change.id === pendingId);
  if (change && change.status !== "queued") {
    showChange(change);
    pendingId = undefined;
  }
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  const change = await workspaceDashboard.setSetting(node, name, input.valueAsNumber, event.submitter.value);
  pendingId = change.status === "queued" ? change.id : undefined;
  showChange(changes.find(item => item.id === pendingId) ?? change);
});
