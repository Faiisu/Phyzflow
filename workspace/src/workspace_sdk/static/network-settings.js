const form = document.querySelector("#network-form");
const fields = form.querySelector("fieldset");
const listener = document.querySelector("#active-listener");
const status = document.querySelector("#network-status");
const problem = document.querySelector("#network-error");

async function loadSettings() {
  try {
    const response = await fetch("/network-settings");
    if (!response.ok) throw new Error("Could not load network settings. Reload to try again.");
    const { active, saved } = await response.json();
    listener.textContent = `${active.ipv4}:${active.port}`;
    form.elements.ipv4.value = saved.ipv4;
    form.elements.port.value = saved.port;
    fields.disabled = false;
  } catch (error) {
    listener.textContent = "Unavailable";
    problem.textContent = error.message;
    problem.hidden = false;
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  fields.disabled = true;
  problem.hidden = true;
  status.textContent = "Saving…";
  try {
    const response = await fetch("/network-settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ipv4: form.elements.ipv4.value.trim(),
        port: form.elements.port.valueAsNumber,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error ?? "Could not save network settings.");
    status.textContent = "Settings saved. Restart Phyzflow to apply them.";
  } catch (error) {
    status.textContent = "";
    problem.textContent = error.message;
    problem.hidden = false;
  } finally {
    fields.disabled = false;
  }
});

loadSettings();
