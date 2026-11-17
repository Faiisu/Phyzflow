const status = document.querySelector("#status");
const batch = document.querySelector("#batch");
const progress = document.querySelector("#progress");
const count = document.querySelector("#reading-count");

workspaceDashboard.subscribe(({ nodeId, run, liveMetrics }) => {
  const step = run?.steps.find(item => item.id === nodeId);
  status.textContent = step?.status ?? "Run the flow to collect readings.";
  const readings = step?.status === "running" ? liveMetrics[nodeId]?.Readings : step?.metrics?.Readings;
  batch.hidden = !readings;
  if (readings) {
    progress.max = readings.Target;
    progress.value = readings.Collected;
    count.textContent = `${readings.Collected} / ${readings.Target}`;
  }
});
