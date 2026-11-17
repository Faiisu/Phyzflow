const canvas = document.querySelector("#chart");
const context = canvas.getContext("2d");
const chartStatus = document.querySelector("#chart-status");
const count = document.querySelector("#count");
const output = document.querySelector("#scaled");
const elapsed = document.querySelector("#elapsed");
const visibleSeconds = 30;
const compactNumber = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const points = [];
let runId;
let showLive = true;

function draw() {
  const { width, height } = canvas.getBoundingClientRect();
  if (width <= 0 || height <= 0) return;
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  context.setTransform(scale, 0, 0, scale, 0, 0);

  const samples = showLive ? points : [];
  const last = samples.at(-1);
  const currentTime = last?.time ?? 0;
  const end = currentTime >= visibleSeconds ? currentTime : Math.max(5, Math.ceil(currentTime / 5) * 5);
  const start = Math.max(0, end - visibleSeconds);
  const peak = Math.max(10, last?.count ?? 0, last?.output ?? 0);
  const magnitude = 10 ** Math.floor(Math.log10(peak / 4));
  const step = magnitude * [1, 2, 5, 10].find(value => value * magnitude >= peak / 4);
  const ceiling = Math.ceil(peak / step) * step;

  const left = 56;
  const right = width - 15;
  const top = 21;
  const bottom = height - 42;
  const x = time => left + (time - start) / (end - start) * (right - left);
  const y = value => bottom - value / ceiling * (bottom - top);
  context.font = '11px "IBM Plex Mono", monospace';
  context.fillStyle = "#aebbd0";
  context.strokeStyle = "#33435a";
  context.textAlign = "left";
  context.fillText("Value", 0, 10);
  context.textAlign = "right";

  for (let value = 0; value <= ceiling; value += step) {
    const position = y(value);
    context.fillText(compactNumber.format(value), left - 9, position + 4);
    context.beginPath();
    context.moveTo(left, position);
    context.lineTo(right, position);
    context.stroke();
  }

  context.textAlign = "center";
  const interval = width < 420 || end - start > 10 ? 5 : 2;
  for (let time = Math.ceil(start / interval) * interval; time <= end; time += interval) {
    const position = x(time);
    context.fillText(String(time), position, bottom + 16);
    context.beginPath();
    context.moveTo(position, top);
    context.lineTo(position, bottom);
    context.stroke();
  }
  context.fillText("Elapsed time (s)", (left + right) / 2, height - 5);

  if (!last) return;
  context.save();
  context.beginPath();
  context.rect(left - 4, top - 4, right - left + 8, bottom - top + 8);
  context.clip();
  for (const [key, color] of [["output", "#ffbf78"], ["count", "#86c7ff"]]) {
    context.strokeStyle = context.fillStyle = color;
    context.lineWidth = 2;
    context.beginPath();
    let prior = samples[0];
    context.moveTo(x(prior.time), y(prior[key]));
    for (const sample of samples.slice(1)) {
      context.lineTo(x(sample.time), y(prior[key]));
      context.lineTo(x(sample.time), y(sample[key]));
      prior = sample;
    }
    context.stroke();
    context.beginPath();
    context.arc(x(last.time), y(last[key]), 3, 0, Math.PI * 2);
    context.fill();
  }
  context.restore();
}

new ResizeObserver(draw).observe(canvas);
workspaceDashboard.subscribe(({ mode, run, liveMetrics }) => {
  showLive = mode === "live";
  if (showLive && run?.id !== runId) {
    runId = run?.id;
    points.length = 0;
  }

  const metrics = showLive && liveMetrics["Multiply by Ten"]?.Counter;
  if (metrics) {
    const time = metrics["Elapsed seconds"];
    if ([time, metrics.Count, metrics.Output].every(Number.isFinite) && time > (points.at(-1)?.time ?? -1)) {
      points.push({ time, count: metrics.Count, output: metrics.Output });
      const cutoff = time - visibleSeconds;
      while (points.length > 2 && points[1].time < cutoff) points.shift();
    }
  }

  const last = showLive ? points.at(-1) : undefined;
  count.value = last?.count ?? "—";
  output.value = last?.output ?? "—";
  elapsed.value = last ? `${last.time.toFixed(1)} s` : "—";
  chartStatus.textContent = !showLive
    ? "Live samples are available in Live run."
    : run?.status === "running"
      ? last ? "Counting live" : "Waiting for the first count…"
      : last ? `Run ${run.status}` : "Run the flow to start counting.";
  draw();
});
