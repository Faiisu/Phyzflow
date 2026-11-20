const dashboard = document.querySelector("#workspace-dashboard");
const frame = dashboard.querySelector("iframe");
const open = document.querySelector("#open-workspace-dashboard");

open.addEventListener("click", () => {
  frame.src = "./dashboard/";
  dashboard.showModal();
});

dashboard.addEventListener("close", () => {
  if (dashboard.open) return;
  frame.removeAttribute("src");
  open.focus();
});
