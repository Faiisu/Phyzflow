from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
import unittest

from anyio.from_thread import start_blocking_portal
from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application, sign_in
from test_application_edit_locks import issue_token
from test_application_mcp import call, mcp_client, output


LIVE_COUNTER = Path(__file__).resolve().parents[1] / "sheets-demo" / "flows" / "Live Counter"


def create_workspace_and_token(page, url: str, state: Path, root: Path, client_name: str = "Dashboard Author"):
    sign_in(page, url, state)
    created = page.request.post(
        f"{url}/workspaces", headers={"Origin": url}, data={"path": str(root)},
    )
    if created.status != 201:
        raise AssertionError(f"Could not create workspace: {created.status} {created.text()}")
    workspace_id = created.json()["id"]
    token = issue_token(page, url, client_name, [workspace_id])
    return workspace_id, token


THEMED_DASHBOARD_HTML = """<main id="panel-root" data-state="idle">
  <header>
    <div>
      <h1>Signal Telemetry</h1>
      <p class="state-banner" id="run-summary" role="status" data-state="idle">Run the flow to collect live telemetry.</p>
    </div>
    <div class="header-actions">
      <span id="state-badge" class="state-badge" data-state="idle">Idle</span>
      <button type="button" id="toggle-series" aria-pressed="true">Compare Output</button>
    </div>
  </header>
  <dl class="metric-grid" aria-label="Telemetry metrics">
    <div class="metric-card series-a">
      <dt class="metric-label">Sample</dt>
      <dd><output id="sample-value" class="metric-value" data-empty="true" aria-live="off">—</output></dd>
    </div>
    <div class="metric-card series-b">
      <dt class="metric-label">Scaled</dt>
      <dd><output id="scaled-value" class="metric-value" data-empty="true" aria-live="off">—</output></dd>
    </div>
    <div class="metric-card">
      <dt class="metric-label">Elapsed</dt>
      <dd><output id="elapsed-value" class="metric-value" data-empty="true" aria-live="off">—</output></dd>
    </div>
  </dl>
  <canvas id="telemetry-chart" role="img" aria-label="Telemetry chart of sample and scaled values"></canvas>
  <div class="chart-legend" aria-label="Series legend">
    <span class="chart-legend-item"><span class="chart-legend-swatch swatch-a"></span>Sample</span>
    <span class="chart-legend-item"><span class="chart-legend-swatch swatch-b"></span>Scaled (×10)</span>
  </div>
</main>
"""

THEMED_DASHBOARD_CSS = """.header-actions { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.series-a dd, .series-a output { color: var(--series-1); }
.series-b dd, .series-b output { color: var(--series-2); }
.swatch-a { background: var(--series-1); }
.swatch-b { background: var(--series-2); }
"""

THEMED_DASHBOARD_JS = """const root = document.querySelector("#panel-root");
const summary = document.querySelector("#run-summary");
const badge = document.querySelector("#state-badge");
const toggleSeries = document.querySelector("#toggle-series");
const sampleOut = document.querySelector("#sample-value");
const scaledOut = document.querySelector("#scaled-value");
const elapsedOut = document.querySelector("#elapsed-value");
const canvas = document.querySelector("#telemetry-chart");
const ctx = canvas.getContext("2d");
const samples = [];
let activeRunId = null;
let showScaled = true;

toggleSeries.addEventListener("click", () => {
  showScaled = !showScaled;
  toggleSeries.setAttribute("aria-pressed", String(showScaled));
  drawChart();
});

function setOutput(el, value) {
  const missing = value === null || value === undefined;
  el.value = missing ? "—" : String(value);
  el.dataset.empty = String(missing);
}

function drawChart() {
  const { width, height } = canvas.getBoundingClientRect();
  if (width <= 0 || height <= 0) return;
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  ctx.setTransform(scale, 0, 0, scale, 0, 0);

  const styles = getComputedStyle(document.documentElement);
  const muted = styles.getPropertyValue("--muted").trim() || "#aebbd0";
  const grid = styles.getPropertyValue("--line").trim() || "#3b4b63";
  const series1 = styles.getPropertyValue("--series-1").trim() || "#86c7ff";
  const series2 = styles.getPropertyValue("--series-2").trim() || "#ffbf78";

  const left = 44;
  const right = Math.max(left + 20, width - 12);
  const top = 16;
  const bottom = Math.max(top + 20, height - 28);
  const maxVal = Math.max(10, ...samples.map(item => Math.max(item.sample, showScaled ? item.scaled : 0)));

  ctx.strokeStyle = grid;
  ctx.fillStyle = muted;
  ctx.font = '11px "IBM Plex Mono", monospace';
  ctx.textAlign = "right";
  for (let i = 0; i <= 2; i++) {
    const val = Math.round(maxVal * i / 2);
    const y = bottom - (bottom - top) * (i / 2);
    ctx.fillText(String(val), left - 6, y + 4);
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
  }
  ctx.textAlign = "center";
  ctx.fillText("Seconds", (left + right) / 2, height - 6);

  if (!samples.length) return;
  const span = Math.max(1, samples.at(-1).elapsed);
  const seriesList = showScaled ? [["scaled", series2], ["sample", series1]] : [["sample", series1]];
  for (const [key, color] of seriesList) {
    ctx.strokeStyle = ctx.fillStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    samples.forEach((pt, idx) => {
      const x = left + (pt.elapsed / span) * (right - left);
      const y = bottom - (pt[key] / maxVal) * (bottom - top);
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    const lastPt = samples.at(-1);
    const lx = left + (lastPt.elapsed / span) * (right - left);
    const ly = bottom - (lastPt[key] / maxVal) * (bottom - top);
    ctx.beginPath();
    ctx.arc(lx, ly, 3, 0, Math.PI * 2);
    ctx.fill();
  }
}

new ResizeObserver(drawChart).observe(canvas);

workspaceDashboard.subscribe(({ run, liveMetrics }) => {
  if (run?.id !== activeRunId) {
    activeRunId = run?.id ?? null;
    samples.length = 0;
  }

  const telemetry = liveMetrics?.["Emit Telemetry"]?.Telemetry;
  const summaryMetrics = run?.steps?.find(step => step.id === "Emit Telemetry")?.metrics?.Telemetry;
  const reading = telemetry ?? summaryMetrics;
  if (reading && [reading.Sample, reading.Scaled, reading.Elapsed].every(Number.isFinite)) {
    const last = samples.at(-1);
    if (!last || reading.Sample !== last.sample || reading.Elapsed > last.elapsed) {
      samples.push({ sample: reading.Sample, scaled: reading.Scaled, elapsed: reading.Elapsed });
    }
  }

  const latest = samples.at(-1);
  setOutput(sampleOut, latest?.sample);
  setOutput(scaledOut, latest?.scaled);
  setOutput(elapsedOut, latest ? `${latest.elapsed.toFixed(1)} s` : null);

  const failedStep = run?.steps?.find(step => step.status === "failed");
  let state = "idle";
  let label = "Idle";
  let note = "Run the flow to collect live telemetry.";
  if (run?.status === "running") {
    state = latest ? "live" : "running";
    label = latest ? "Live" : "Running";
    note = latest ? `Streaming sample ${latest.sample}` : "Waiting for telemetry…";
  } else if (run?.status === "failed" || failedStep) {
    state = "error";
    label = "Error";
    note = failedStep
      ? `${failedStep.id} failed (${failedStep.error_type || "Error"})`
      : "Run failed";
  } else if (run?.status === "succeeded") {
    state = "succeeded";
    label = "Succeeded";
    note = latest ? `Completed at sample ${latest.sample}` : "Completed";
  } else if (run?.status === "stopped") {
    state = "stopped";
    label = "Stopped";
    note = "Run stopped";
  }

  root.dataset.state = state;
  summary.dataset.state = state;
  badge.dataset.state = state;
  badge.textContent = label;
  summary.textContent = note;
  drawChart();
});
"""


class ApplicationDashboardTests(unittest.TestCase):
    def test_mcp_inspect_dashboards_and_author_assets_under_edit_lock(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "DashboardLab"
            denied_root = parent / "PrivateLab"
            state = parent / "state"
            with application(state) as url:
                workspace_id, token = create_workspace_and_token(page, url, state, root)
                denied_created = page.request.post(
                    f"{url}/workspaces", headers={"Origin": url}, data={"path": str(denied_root)},
                )
                self.assertEqual(denied_created.status, 201)
                denied_id = denied_created.json()["id"]

                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    tools = {tool.name for tool in portal.call(client.list_tools).tools}
                    self.assertIn("inspect_dashboards", tools)

                    denied = call(portal, client, "inspect_dashboards", workspace_id=denied_id)
                    self.assertTrue(denied.is_error)
                    self.assertIn("Workspace access denied", denied.content[0].text)

                    initial = call(portal, client, "inspect_dashboards", workspace_id=workspace_id, sheet="Main")
                    self.assertFalse(initial.is_error, initial.content[0].text)
                    info = output(initial)
                    self.assertEqual(info["sheet"], "Main")
                    self.assertEqual(info["workflow"], [])
                    self.assertEqual(info["nodes"], [])
                    self.assertEqual(info["layout"]["workflow_directory"], "dashboards/workflow")
                    self.assertTrue(info["theme_reference"]["shared_stylesheet_injected"])
                    self.assertIn("--bg", info["theme_reference"]["tokens"]["color"])
                    self.assertIn("--series-1", info["theme_reference"]["tokens"]["color"])
                    self.assertIn("--mono", info["theme_reference"]["tokens"]["typography"])
                    self.assertIn("subscribe(listener)", info["theme_reference"]["bridge"]["methods"])

                    opened = call(portal, client, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(opened.is_error, opened.content[0].text)
                    session_id = output(opened)["session_id"]

                    for path, source in (
                        ("dashboards/workflow/index.html", "<main><h1>Overview</h1></main>\n"),
                        ("dashboards/workflow/style.css", "main { gap: 1rem; }\n"),
                        ("dashboards/workflow/dashboard.js", "workspaceDashboard.subscribe(() => {});\n"),
                        ("dashboards/nodes/Start/index.html", "<main><h1>Start</h1></main>\n"),
                    ):
                        wrote = call(
                            portal, client, "write_file",
                            workspace_id=workspace_id, path=path, revision="missing",
                            source=source, sheet="Main", session_id=session_id,
                        )
                        self.assertFalse(wrote.is_error, wrote.content[0].text)
                        if path == "dashboards/workflow/style.css":
                            css_rev = output(wrote)["revision"]

                    patched = call(
                        portal, client, "apply_patch",
                        workspace_id=workspace_id, path="dashboards/workflow/style.css",
                        revision=css_rev, old_text="gap: 1rem;", new_text="gap: var(--space-3);",
                        sheet="Main", session_id=session_id,
                    )
                    self.assertFalse(patched.is_error, patched.content[0].text)
                    self.assertIn(
                        "gap: var(--space-3);",
                        (root / "dashboards/workflow/style.css").read_text(encoding="utf-8"),
                    )

                    after = output(call(portal, client, "inspect_dashboards", workspace_id=workspace_id, sheet="Main"))
                    self.assertEqual(after["workflow"], [{
                        "id": "default",
                        "label": "Overview",
                        "directory": "dashboards/workflow",
                        "assets": [
                            "dashboards/workflow/index.html",
                            "dashboards/workflow/style.css",
                            "dashboards/workflow/dashboard.js",
                        ],
                        "ready": True,
                    }])
                    self.assertEqual(after["nodes"], [{
                        "step_id": "Start",
                        "directory": "dashboards/nodes/Start",
                        "panels": [{
                            "id": "default",
                            "label": "Overview",
                            "directory": "dashboards/nodes/Start",
                            "assets": ["dashboards/nodes/Start/index.html"],
                            "ready": True,
                        }],
                    }])

                    closed = call(
                        portal, client, "close_sheet",
                        workspace_id=workspace_id, sheet="Main", session_id=session_id,
                    )
                    self.assertFalse(closed.is_error, closed.content[0].text)

    def test_authored_panel_uses_shared_theme_in_idle_state_and_sandbox(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "ThemedIdle"
            state = parent / "state"
            with application(state) as url:
                workspace_id, token = create_workspace_and_token(page, url, state, root)

                page.goto(f"{url}/workspaces/{workspace_id}/")
                flow = page.frame_locator('iframe[title="Main flow"]')
                expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()
                expect(flow.locator("#visualize-list")).to_contain_text("No dashboards found in this flow.")

                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    opened = call(portal, client, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(opened.is_error, opened.content[0].text)
                    session_id = output(opened)["session_id"]

                    for path, source in (
                        ("dashboards/workflow/index.html", THEMED_DASHBOARD_HTML),
                        ("dashboards/workflow/style.css", THEMED_DASHBOARD_CSS),
                        ("dashboards/workflow/dashboard.js", THEMED_DASHBOARD_JS),
                    ):
                        wrote = call(
                            portal, client, "write_file",
                            workspace_id=workspace_id, path=path, revision="missing",
                            source=source, sheet="Main", session_id=session_id,
                        )
                        self.assertFalse(wrote.is_error, wrote.content[0].text)

                    closed = call(
                        portal, client, "close_sheet",
                        workspace_id=workspace_id, sheet="Main", session_id=session_id,
                    )
                    self.assertFalse(closed.is_error, closed.content[0].text)

                panel_button = flow.locator('[data-vis-node=""][data-vis-panel="default"]')
                expect(panel_button).to_be_visible(timeout=5000)
                panel_button.click()

                iframe = flow.locator("#dashboard-preview iframe")
                expect(iframe).to_be_visible()
                self.assertEqual(iframe.get_attribute("sandbox"), "allow-scripts allow-forms")

                panel = flow.frame_locator("#dashboard-preview iframe")
                expect(panel.get_by_role("heading", name="Signal Telemetry")).to_be_visible()
                expect(panel.locator("#state-badge")).to_have_text("Idle")
                expect(panel.locator("#state-badge")).to_have_attribute("data-state", "idle")
                expect(panel.locator("#sample-value")).to_have_text("—")
                expect(panel.locator("#sample-value")).to_have_attribute("data-empty", "true")

                theme_check = panel.locator("html").evaluate("""() => {
                    const styles = Array.from(document.head.querySelectorAll("style"));
                    const themeIndex = styles.findIndex(el => el.dataset.phyzflowTheme === "true");
                    const panelIndex = styles.findIndex(el => el.dataset.phyzflowTheme !== "true");
                    const rootStyle = getComputedStyle(document.documentElement);
                    const bodyStyle = getComputedStyle(document.body);
                    const mainStyle = getComputedStyle(document.querySelector("main"));
                    const sampleStyle = getComputedStyle(document.querySelector("#sample-value"));
                    const scaledStyle = getComputedStyle(document.querySelector("#scaled-value"));
                    const toggle = document.querySelector("#toggle-series");
                    let parentIsolated = false;
                    try { parent.document.body; } catch { parentIsolated = true; }
                    return {
                        themeIndex,
                        panelIndex,
                        parentIsolated,
                        rootBg: rootStyle.backgroundColor,
                        rootColor: rootStyle.color,
                        bodyColor: bodyStyle.color,
                        bodyFont: bodyStyle.fontFamily,
                        mainPaddingTop: parseFloat(mainStyle.paddingTop),
                        sampleColor: sampleStyle.color,
                        sampleFont: sampleStyle.fontFamily,
                        sampleTabular: sampleStyle.fontVariantNumeric,
                        scaledColor: scaledStyle.color,
                        accentVar: rootStyle.getPropertyValue("--accent").trim(),
                        hasToggle: Boolean(toggle),
                    };
                }""")
                self.assertGreaterEqual(theme_check["themeIndex"], 0)
                self.assertGreater(theme_check["panelIndex"], theme_check["themeIndex"])
                self.assertTrue(theme_check["parentIsolated"])
                self.assertEqual(theme_check["rootBg"], "rgb(20, 29, 43)")
                self.assertEqual(theme_check["rootColor"], "rgb(236, 240, 250)")
                self.assertEqual(theme_check["bodyColor"], "rgb(236, 240, 250)")
                self.assertIn("Instrument Sans", theme_check["bodyFont"])
                self.assertGreater(theme_check["mainPaddingTop"], 0)
                self.assertEqual(theme_check["sampleColor"], "rgb(134, 199, 255)")
                self.assertEqual(theme_check["scaledColor"], "rgb(255, 191, 120)")
                self.assertIn("IBM Plex Mono", theme_check["sampleFont"])
                self.assertIn("tabular-nums", theme_check["sampleTabular"])
                self.assertTrue(theme_check["accentVar"])

                toggle = panel.locator("#toggle-series")
                toggle.focus()
                panel.locator("body").press("Tab")
                toggle.focus()
                focus_outline = toggle.evaluate("""el => {
                    el.focus({ focusVisible: true });
                    return getComputedStyle(el).outlineColor;
                }""")
                self.assertEqual(focus_outline, "rgb(134, 199, 255)")

    def test_authored_panel_tracks_live_succeeded_error_and_narrow_panel_states(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "LiveDashboardFlow"
            state = parent / "state"
            with application(state) as url:
                workspace_id, token = create_workspace_and_token(page, url, state, root)

                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    opened = call(portal, client, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(opened.is_error, opened.content[0].text)
                    session_id = output(opened)["session_id"]

                    entry_read = output(call(
                        portal, client, "read_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py", sheet="Main",
                    ))
                    entry_source = (
                        "from pathlib import Path\n"
                        "from time import monotonic, sleep\n\n"
                        "from workspace_sdk import publish_metrics, record_metrics\n\n"
                        "def emit_telemetry():\n"
                        "    publish_metrics('Telemetry', {'Sample': 7, 'Scaled': 70, 'Elapsed': 1.2})\n"
                        "    deadline = monotonic() + 20\n"
                        "    while not Path('step-2').exists() and monotonic() < deadline:\n"
                        "        sleep(0.02)\n"
                        "    publish_metrics('Telemetry', {'Sample': 14, 'Scaled': 140, 'Elapsed': 2.5})\n"
                        "    record_metrics('Telemetry', {'Sample': 14, 'Scaled': 140, 'Elapsed': 2.5})\n"
                        "    return {'sample': 14, 'scaled': 140}\n\n"
                        "def validate_output(payload, fail):\n"
                        "    if fail:\n"
                        "        raise ValueError('threshold exceeded')\n"
                        "    return payload\n"
                    )
                    self.assertFalse(call(
                        portal, client, "write_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py",
                        revision=entry_read["revision"], source=entry_source,
                        sheet="Main", session_id=session_id,
                    ).is_error)

                    flow_read = output(call(
                        portal, client, "read_file",
                        workspace_id=workspace_id, path="flow.py", sheet="Main",
                    ))
                    flow_source = (
                        "from workspace_sdk import Flow, Input, Output, Step\n"
                        "from nodes.start.entrypoint import emit_telemetry, validate_output\n"
                        "flow = Flow(\n"
                        "    ('fail',),\n"
                        "    (\n"
                        "        Step('Emit Telemetry', emit_telemetry, {}),\n"
                        "        Step('Validate Output', validate_output, {'payload': Output('Emit Telemetry'), 'fail': Input('fail')}),\n"
                        "    ),\n"
                        "    Output('Validate Output'),\n"
                        ")\n"
                    )
                    self.assertFalse(call(
                        portal, client, "write_file",
                        workspace_id=workspace_id, path="flow.py",
                        revision=flow_read["revision"], source=flow_source,
                        sheet="Main", session_id=session_id,
                    ).is_error)

                    self.assertFalse(call(
                        portal, client, "write_file",
                        workspace_id=workspace_id, path="sample.json", revision="missing",
                        source='{"input": {"fail": false}}\n',
                        sheet="Main", session_id=session_id,
                    ).is_error)

                    for path, source in (
                        ("dashboards/workflow/index.html", THEMED_DASHBOARD_HTML),
                        ("dashboards/workflow/style.css", THEMED_DASHBOARD_CSS),
                        ("dashboards/workflow/dashboard.js", THEMED_DASHBOARD_JS),
                    ):
                        wrote = call(
                            portal, client, "write_file",
                            workspace_id=workspace_id, path=path, revision="missing",
                            source=source, sheet="Main", session_id=session_id,
                        )
                        self.assertFalse(wrote.is_error, wrote.content[0].text)

                    validated = call(portal, client, "validate_flow", workspace_id=workspace_id, sheet="Main", session_id=session_id)
                    self.assertFalse(validated.is_error, validated.content[0].text)
                    self.assertTrue(output(validated)["valid"])

                    closed = call(
                        portal, client, "close_sheet",
                        workspace_id=workspace_id, sheet="Main", session_id=session_id,
                    )
                    self.assertFalse(closed.is_error, closed.content[0].text)

                page.goto(f"{url}/workspaces/{workspace_id}/")
                flow = page.frame_locator('iframe[title="Main flow"]')
                expect(flow.get_by_role("button", name="Inspect Emit Telemetry")).to_be_visible()

                flow.locator('[data-vis-node=""][data-vis-panel="default"]').click()
                board = flow.locator("#dashboard")
                expect(board).to_be_visible()
                panel = flow.frame_locator("#dashboard-preview iframe")
                expect(panel.locator("#state-badge")).to_have_text("Idle")

                page.get_by_role("button", name="Run flow", exact=True).click()
                expect(flow.locator("#status")).to_have_text("Running")
                expect(panel.locator("#state-badge")).to_have_text("Live")
                expect(panel.locator("#state-badge")).to_have_attribute("data-state", "live")
                expect(panel.locator("#sample-value")).to_have_text("7")
                expect(panel.locator("#sample-value")).to_have_attribute("data-empty", "false")
                expect(panel.locator("#scaled-value")).to_have_text("70")
                expect(panel.locator("#elapsed-value")).to_have_text("1.2 s")

                def canvas_series_presence():
                    return panel.locator("#telemetry-chart").evaluate("""canvas => {
                        const ctx = canvas.getContext("2d");
                        const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                        const hasColor = (r, g, b) => {
                            for (let i = 0; i < pixels.length; i += 4) {
                                if (pixels[i] === r && pixels[i + 1] === g && pixels[i + 2] === b && pixels[i + 3] > 200) {
                                    return true;
                                }
                            }
                            return false;
                        };
                        return {
                            series1: hasColor(134, 199, 255),
                            series2: hasColor(255, 191, 120),
                        };
                    }""")

                self.assertEqual(canvas_series_presence(), {"series1": True, "series2": True})
                panel.locator("#toggle-series").click()
                expect(panel.locator("#toggle-series")).to_have_attribute("aria-pressed", "false")
                self.assertEqual(canvas_series_presence(), {"series1": True, "series2": False})
                panel.locator("#toggle-series").click()
                expect(panel.locator("#toggle-series")).to_have_attribute("aria-pressed", "true")

                (root / "step-2").touch()
                expect(flow.locator("#status")).to_have_text("Succeeded", timeout=10_000)
                expect(panel.locator("#state-badge")).to_have_text("Succeeded")
                expect(panel.locator("#state-badge")).to_have_attribute("data-state", "succeeded")
                expect(panel.locator("#sample-value")).to_have_text("14")
                expect(panel.locator("#scaled-value")).to_have_text("140")
                expect(panel.locator("#elapsed-value")).to_have_text("2.5 s")

                host_succeeded_color = flow.locator("#status").evaluate("el => getComputedStyle(el).color")
                panel_succeeded_color = panel.locator("#state-badge").evaluate("el => getComputedStyle(el).color")
                self.assertEqual(panel_succeeded_color, host_succeeded_color)

                flow.get_by_role("textbox", name="Run input JSON").fill('{"fail": true}')
                page.get_by_role("button", name="Run flow", exact=True).click()
                expect(flow.locator("#status")).to_have_text("Failed", timeout=10_000)
                expect(panel.locator("#state-badge")).to_have_text("Error")
                expect(panel.locator("#state-badge")).to_have_attribute("data-state", "error")
                expect(panel.locator("#run-summary")).to_have_text("Validate Output failed (ValueError)")
                expect(panel.locator("#run-summary")).to_have_attribute("data-state", "error")

                host_failed_color = flow.locator("#status").evaluate("el => getComputedStyle(el).color")
                panel_error_color = panel.locator("#state-badge").evaluate("el => getComputedStyle(el).color")
                self.assertEqual(panel_error_color, host_failed_color)

                resize_handle = board.locator("#dashboard-resize")
                before_box = board.bounding_box()
                handle_box = resize_handle.bounding_box()
                hx = handle_box["x"] + handle_box["width"] / 2
                hy = handle_box["y"] + handle_box["height"] / 2
                delta_x = 320 - before_box["width"]
                page.mouse.move(hx, hy)
                page.mouse.down()
                page.mouse.move(hx + delta_x, hy, steps=5)
                page.mouse.up()

                narrow_box = board.bounding_box()
                self.assertLessEqual(narrow_box["width"], 360)
                self.assertGreaterEqual(narrow_box["width"], 280)

                narrow_layout = panel.locator("html").evaluate("""() => {
                    const doc = document.documentElement;
                    const header = document.querySelector("header");
                    const main = document.querySelector("main");
                    const canvas = document.querySelector("#telemetry-chart");
                    const canvasRect = canvas.getBoundingClientRect();
                    const sampleRect = document.querySelector("#sample-value").getBoundingClientRect();
                    return {
                        clientWidth: doc.clientWidth,
                        scrollWidth: doc.scrollWidth,
                        headerDirection: getComputedStyle(header).flexDirection,
                        mainPaddingTop: parseFloat(getComputedStyle(main).paddingTop),
                        canvasWidth: canvasRect.width,
                        canvasHeight: canvasRect.height,
                        canvasRight: canvasRect.right,
                        sampleVisible: sampleRect.width > 0 && sampleRect.height > 0 && sampleRect.right <= doc.clientWidth + 1,
                    };
                }""")
                self.assertLessEqual(narrow_layout["scrollWidth"], narrow_layout["clientWidth"])
                self.assertEqual(narrow_layout["headerDirection"], "column")
                self.assertLessEqual(narrow_layout["mainPaddingTop"], 10)
                self.assertGreater(narrow_layout["canvasWidth"], 200)
                self.assertGreater(narrow_layout["canvasHeight"], 100)
                self.assertLessEqual(narrow_layout["canvasRight"], narrow_layout["clientWidth"] + 1)
                self.assertTrue(narrow_layout["sampleVisible"])
                self.assertEqual(canvas_series_presence(), {"series1": True, "series2": True})

    def test_inspect_dashboards_handles_subpanels_encoded_steps_symlinks_and_theme_reference(self):
        from workspace_sdk.dashboard import DASHBOARD_THEME_REFERENCE
        from workspace_sdk.web import STATIC

        theme_css = (STATIC / "dashboard-ui.js").read_text(encoding="utf-8")
        for group in DASHBOARD_THEME_REFERENCE["tokens"].values():
            for token in group:
                self.assertIn(token, theme_css)
        for state_name in DASHBOARD_THEME_REFERENCE["states"]:
            self.assertIn(f'data-state="{state_name}"', theme_css)
        for class_name in DASHBOARD_THEME_REFERENCE["classes"]:
            self.assertIn(f".{class_name}", theme_css)
        self.assertIn(f"max-width: {DASHBOARD_THEME_REFERENCE['narrow_breakpoint_px']}px", theme_css)

        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "MultiScope"
            state = parent / "state"
            with application(state) as url:
                workspace_id, token = create_workspace_and_token(page, url, state, root)

                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    opened = call(portal, client, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    session_id = output(opened)["session_id"]

                    for path, source in (
                        ("dashboards/workflow/panels/rate-chart/index.html", "<main><h1>Rate</h1><button id='custom-btn'>Inspect</button></main>\n"),
                        ("dashboards/workflow/panels/rate-chart/style.css", "button { background: transparent; border: 0; }\n"),
                        ("dashboards/nodes/Step%2FOne/index.html", "<main><h1>Encoded</h1></main>\n"),
                    ):
                        wrote = call(
                            portal, client, "write_file",
                            workspace_id=workspace_id, path=path, revision="missing",
                            source=source, sheet="Main", session_id=session_id,
                        )
                        self.assertFalse(wrote.is_error, wrote.content[0].text)

                    (root / "dashboards/workflow/index.html").symlink_to(root / "flow.py")
                    inspected = output(call(portal, client, "inspect_dashboards", workspace_id=workspace_id, sheet="Main"))
                    self.assertEqual(
                        [item["id"] for item in inspected["workflow"]],
                        ["rate-chart"],
                    )
                    self.assertEqual(inspected["nodes"][0]["step_id"], "Step/One")
                    closed = call(
                        portal, client, "close_sheet",
                        workspace_id=workspace_id, sheet="Main", session_id=session_id,
                    )
                    self.assertFalse(closed.is_error, closed.content[0].text)

                page.goto(f"{url}/workspaces/{workspace_id}/")
                flow = page.frame_locator('iframe[title="Main flow"]')
                flow.locator("#visualize-refresh").click()
                sub_btn = flow.locator('[data-vis-node=""][data-vis-panel="rate-chart"]')
                expect(sub_btn).to_be_visible()
                sub_btn.click()
                panel = flow.frame_locator("#dashboard-preview iframe")
                custom_btn = panel.locator("#custom-btn")
                expect(custom_btn).to_be_visible()
                custom_btn.hover()
                self.assertEqual(
                    custom_btn.evaluate("el => getComputedStyle(el).backgroundColor"),
                    "rgba(0, 0, 0, 0)",
                )
                custom_btn.click()
                page.keyboard.press("Tab")
                custom_btn.focus()
                page.keyboard.press("Shift+Tab")
                page.keyboard.press("Tab")
                self.assertEqual(
                    custom_btn.evaluate("el => getComputedStyle(el).outlineColor"),
                    "rgb(134, 199, 255)",
                )

    def test_live_counter_example_inspects_and_renders_with_injected_shared_theme(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "LiveCounterApp"
            copytree(LIVE_COUNTER, root, ignore=ignore_patterns(".workspace", "__pycache__", ".venv"))
            state = parent / "state"
            with application(state) as url:
                sign_in(page, url, state)
                added = page.request.post(
                    f"{url}/workspaces", headers={"Origin": url}, data={"path": str(root), "action": "add"},
                )
                self.assertEqual(added.status, 201, added.text())
                workspace_id = added.json()["id"]
                token = issue_token(page, url, "Inspector", [workspace_id])

                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    inspected = output(call(portal, client, "inspect_dashboards", workspace_id=workspace_id, sheet="Main"))
                    self.assertEqual(
                        inspected["workflow"][0]["assets"],
                        [
                            "dashboards/workflow/index.html",
                            "dashboards/workflow/style.css",
                            "dashboards/workflow/dashboard.js",
                        ],
                    )
                    self.assertEqual(inspected["nodes"][0]["step_id"], "Multiply by Ten")

                page.goto(f"{url}/workspaces/{workspace_id}/")
                flow = page.frame_locator('iframe[title="Main flow"]')
                flow.locator('[data-vis-node=""][data-vis-panel="default"]').click()
                panel = flow.frame_locator("#dashboard-preview iframe")
                expect(panel.get_by_role("heading", name="Live Counter")).to_be_visible()
                expect(panel.locator("#chart-status")).to_have_text("Run the flow to start counting.")

                page.get_by_role("button", name="Run flow", exact=True).click()
                expect(panel.locator("#count")).to_have_text("2", timeout=10_000)
                expect(panel.locator("#scaled")).to_have_text("20")
                has_series = panel.locator("#chart").evaluate("""canvas => {
                    const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
                    const hasColor = (r, g, b) => {
                        for (let i = 0; i < pixels.length; i += 4) {
                            if (pixels[i] === r && pixels[i + 1] === g && pixels[i + 2] === b && pixels[i + 3] === 255) return true;
                        }
                        return false;
                    };
                    return hasColor(134, 199, 255) && hasColor(255, 191, 120);
                }""")
                self.assertTrue(has_series)
                page.get_by_role("button", name="Stop Main").click()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Stopped")
