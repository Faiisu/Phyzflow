import { initDashboards } from "./dashboard-ui.js";
import { element } from "./flow-view/display.js";
import { initGraph } from "./flow-view/graph.js";
import { initInspector } from "./flow-view/inspector.js";
import { initNodeSource } from "./flow-view/node-source.js";
import { initRunControls } from "./flow-view/run-controls.js";

const compactPanelLayout = window.matchMedia("(max-width: 720px)");
const drawerPanels = ["outline", "visualize", "inspector", "run"];
let wideDrawers = [];
const workspaceToolbar = window.parent !== window && window.parent.phyzflowSyncToolbar;
const recoveryView = window.location.pathname.endsWith("/recovery/");
const panelSwitcher = document.querySelector("#panel-switcher");
const panelToggles = [...panelSwitcher.querySelectorAll("button[aria-controls]")];
let dashboards;

const editor = initNodeSource({
  recoveryView, getStopPhase: () => run.stopPhase, isRunInProgress: () => run.runInProgress,
});
const graph = initGraph({
  recoveryView, fitOnOpen: Boolean(workspaceToolbar) || recoveryView,
  onSelect: (step, project) => inspector.inspectNode(step, project),
  revealInspector: () => {
    if (recoveryView || compactPanelLayout.matches) {
      setPanel("inspector", true).querySelector("[data-close-panel]").focus();
    }
  },
  showNodeFiles: editor.showNodeFiles,
});
const inspector = initInspector({
  graph, showNodeFiles: editor.showNodeFiles, openDashboard: id => dashboards.open(id),
});
const run = initRunControls({
  graph, inspector, editor, getDashboards: () => dashboards, setPanel, workspaceToolbar, recoveryView,
});

function setPanel(name, visible) {
  if (visible && compactPanelLayout.matches && drawerPanels.includes(name)) {
    for (const other of drawerPanels) {
      if (other !== name) setPanel(other, false);
    }
  }
  const id = `panel-${name}`;
  const panel = document.getElementById(id);
  panel.hidden = !visible;
  panelToggles.find(toggle => toggle.getAttribute("aria-controls") === id)
    .setAttribute("aria-pressed", String(visible));
  return panel;
}

function syncPanelLayout() {
  if (compactPanelLayout.matches) {
    wideDrawers = drawerPanels.filter(name => !document.getElementById(`panel-${name}`).hidden);
    for (const name of drawerPanels) setPanel(name, false);
  } else {
    for (const name of drawerPanels) setPanel(name, wideDrawers.includes(name));
  }
}

for (const toggle of panelToggles) {
  toggle.addEventListener("click", () => {
    const visible = toggle.getAttribute("aria-pressed") === "false";
    const name = toggle.getAttribute("aria-controls").slice(6);
    const panel = setPanel(name, visible);
    if (visible) panel.scrollIntoView({ block: "nearest" });
    if (visible && compactPanelLayout.matches && drawerPanels.includes(name)) {
      panelSwitcher.open = false;
      panel.querySelector("[data-close-panel]").focus();
    }
  });
}

document.addEventListener("pointerdown", event => {
  if (panelSwitcher.open && !panelSwitcher.contains(event.target)) panelSwitcher.open = false;
});

panelSwitcher.addEventListener("keydown", event => {
  if (event.key !== "Escape" || !panelSwitcher.open) return;
  panelSwitcher.open = false;
  panelSwitcher.querySelector("summary").focus();
});

for (const button of document.querySelectorAll("[data-close-panel]")) {
  button.addEventListener("click", () => {
    setPanel(button.dataset.closePanel, false);
    panelSwitcher.querySelector("summary").focus();
  });
}

if (workspaceToolbar || recoveryView) {
  setPanel("toolbar", false);
  panelSwitcher.querySelector('[aria-controls="panel-toolbar"]').hidden = true;
}
if (recoveryView) {
  document.querySelector("#visualize-list").replaceChildren(
    element("p", "Open or run the flow to load dashboards.", "muted"),
  );
  document.querySelector("#visualize-refresh").disabled = true;
  document.querySelector("#visualize-node").disabled = true;
}
if (compactPanelLayout.matches) syncPanelLayout();
compactPanelLayout.addEventListener("change", syncPanelLayout);


try {
  const [response, consoleResponse] = await Promise.all([
    fetch("project"),
    recoveryView ? null : fetch("console-logs").catch(() => null),
  ]);
  if (!response.ok) throw new Error("Could not load the project graph. Restart Workspace and try again.");
  const project = await response.json();
  const consoleData = consoleResponse?.ok ? await consoleResponse.json().catch(() => null) : null;
  run.loadConsole(consoleData);
  editor.loadProject(project);
  const { canvasLayout } = graph;
  if (!recoveryView) {
    dashboards = initDashboards(
      project,
      canvasLayout.revealDashboard,
      canvasLayout.releaseDashboard,
      canvasLayout.setVisualizeModeLayout,
    );
  }
  const name = document.querySelector("#project-name");
  name.textContent = project.name;
  name.title = project.name;
  name.hidden = false;
  graph.positionGraph(project);
  if (!recoveryView) run.loadSample(project.input);
} catch (error) {
  run.showLoadError(error);
}
