const runs = document.querySelector("#runs");
const metrics = document.querySelector("#live-metrics");
const units = new Intl.NumberFormat("en", { maximumFractionDigits: 2 });
const liveLabels = { "live-count": "Count", "live-scored": "Scored events" };

function makeElement(tag, className, text = "") {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

function emptyLabel(source, fallback) {
  if (source.state === "sheet_unavailable") return "Sheet unavailable";
  if (source.state === "source_unavailable") return "Data unavailable";
  return fallback;
}

function sheetLabel(source) {
  return source.sheet_name ?? (source.sheet === "." ? "Main" : source.sheet.split("/").at(-1));
}

workspaceDashboard.subscribe(({ sources }) => {
  const durations = sources.filter(source => source.id.endsWith("-duration"));
  const maxDuration = Math.max(1, ...durations.map(source => source.value ?? 0));
  runs.replaceChildren(...durations.map(source => {
    const row = makeElement("div", "chart-row");
    const name = makeElement("span", "chart-label", sheetLabel(source));
    const track = makeElement("div", "chart-track");
    track.setAttribute("aria-hidden", "true");
    const bar = makeElement("div", "chart-bar");
    bar.style.width = `${100 * (source.value ?? 0) / maxDuration}%`;
    track.append(bar);
    const duration = source.value === null ? emptyLabel(source, "No saved run") : `${units.format(source.value)} ms`;
    row.append(name, track, makeElement("span", "chart-value", duration));
    return row;
  }));

  metrics.replaceChildren(...sources.filter(source => Object.hasOwn(liveLabels, source.id)).map(source => {
    const card = makeElement("article", "metric-card");
    const label = `${sheetLabel(source)} · ${liveLabels[source.id]}`;
    const value = source.value === null ? emptyLabel(source, "Waiting for a live run") : units.format(source.value);
    card.append(makeElement("p", "metric-label", label), makeElement("p", "metric-value", value));
    return card;
  }));
});
