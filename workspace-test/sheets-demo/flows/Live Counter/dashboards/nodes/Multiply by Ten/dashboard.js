const count = document.querySelector("#input-count");
const output = document.querySelector("#node-output");
const elapsed = document.querySelector("#elapsed");
const status = document.querySelector("#reading-status");

workspaceDashboard.subscribe(({ nodeId, mode, run, liveMetrics }) => {
  const sample = liveMetrics[nodeId]?.Counter;
  const step = run?.steps.find(node => node.id === nodeId);
  const seconds = sample?.["Elapsed seconds"];
  count.value = sample?.Count ?? "—";
  output.value = sample?.Output ?? "—";
  elapsed.value = seconds === undefined ? "—" : `${seconds.toFixed(1)} s`;

  let message = "Run the flow to see live readings.";
  if (mode === "saved") {
    message = "Counter readings are available during a live run.";
  } else if (run?.status === "running") {
    message = sample ? "Live readings" : "Waiting for the first reading…";
  } else if (run) {
    message = sample ? "Last reading from this run." : "This run produced no counter readings.";
  }
  if (step?.status === "failed") message = "The node failed.";
  if (status.textContent !== message) status.textContent = message;
});
