import { createCanvasLayout } from "./canvas-layout.js";
import { initDashboards } from "./dashboard-ui.js";

document.title = "Phyzflow | Workspace Dashboard";
document.body.classList.add("is-visualize-mode", "is-workspace-dashboard");
document.querySelector("#graph-heading").textContent = "Workspace Dashboard";
const viewport = document.querySelector(".graph-scroll");
viewport.setAttribute("aria-label", "Workspace dashboard panels");

const menu = document.querySelector("#panel-switcher");
const drawer = document.querySelector("#panel-visualize");
document.querySelector("#visualize-heading").textContent = "Dashboard panels";
document.querySelector("#enter-visualize-mode").hidden = true;
drawer.querySelector('[data-close-panel="visualize"]').addEventListener("click", () => {
  menu.open = false;
  menu.querySelector("summary").focus();
});
menu.querySelector(".panel-menu").replaceWith(drawer);
menu.addEventListener("keydown", event => {
  if (event.key !== "Escape" || !menu.open || document.querySelector("#dashboard-editor").open) return;
  menu.open = false;
  menu.querySelector("summary").focus();
  event.stopPropagation();
});

const back = document.querySelector("#exit-visualize-mode");
back.hidden = false;
back.textContent = "Back to sheets";

const status = document.createElement("p");
status.id = "workspace-dashboard-message";
status.className = "overlay";
status.setAttribute("role", "status");
document.querySelector(".stage").append(status);

if (document.fullscreenEnabled) {
  const fullscreen = document.createElement("button");
  fullscreen.type = "button";
  fullscreen.className = "text-button";
  fullscreen.textContent = "Full screen dashboard";
  document.addEventListener("fullscreenchange", () => {
    fullscreen.textContent = document.fullscreenElement === document.documentElement
      ? "Exit full screen dashboard" : "Full screen dashboard";
  });
  fullscreen.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement === document.documentElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      status.textContent = "Could not change dashboard full screen mode.";
      status.hidden = false;
    }
  });
  drawer.querySelector(".visualize-actions").append(fullscreen);
}

const layout = createCanvasLayout({
  graph: document.querySelector("#graph"),
  graphPlane: document.querySelector("#graph-plane"),
  graphViewport: viewport,
  layoutSize(element) {
    const { width, height } = element.getBoundingClientRect();
    return { width, height };
  },
  cancelWheelZoom: () => {},
});
layout.setVisualizeModeLayout(true);

initDashboards({ steps: [] }, layout.revealDashboard, layout.releaseDashboard, layout.setVisualizeModeLayout, {
  workspace: {
    status,
    exit() {
      const dialog = window.frameElement?.closest("dialog");
      if (dialog) dialog.close();
      else window.location.assign("../");
    },
  },
});
