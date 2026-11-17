const camera = document.querySelector("#camera");
const placeholder = document.querySelector("#placeholder");
const status = document.querySelector("#status");

workspaceDashboard.subscribe(({ mode, run, liveFrames }) => {
  const frame = liveFrames["Detect People"];
  camera.hidden = !frame;
  placeholder.hidden = Boolean(frame);
  if (frame && camera.getAttribute("src") !== frame) {
    camera.src = frame;
  } else if (!frame) {
    camera.removeAttribute("src");
  }

  status.toggleAttribute("data-live", Boolean(frame));
  const message = frame ? "Camera live"
    : mode === "saved" ? "Select Live run to view the camera."
    : run?.status === "running" ? "Waiting for the first frame…"
    : run?.status === "failed" ? "Camera run failed. Open the Run panel for details."
    : "Select Run flow to start the camera.";
  if (status.textContent !== message) status.textContent = message;
});
