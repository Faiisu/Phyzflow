from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
import json
import unittest

from anyio.from_thread import start_blocking_portal
from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application, sign_in
from test_application_edit_locks import issue_token, wait_for_open_sheet
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
    def test_sheet_dashboard_sources_remain_readable_during_mcp_edit(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "DashboardLocks"
            state = parent / "state"
            copytree(LIVE_COUNTER, root, ignore=ignore_patterns(".workspace", "__pycache__", ".venv"))
            with application(state) as url:
                sign_in(page, url, state)
                added = page.request.post(
                    f"{url}/workspaces", headers={"Origin": url}, data={"path": str(root), "action": "add"},
                )
                self.assertEqual(added.status, 201, added.text())
                workspace_id = added.json()["id"]
                token = issue_token(page, url, "Dashboard Author", [workspace_id])
                workspace = f"{url}/workspaces/{workspace_id}/"
                page.goto(workspace)
                flow = page.frame_locator('iframe[title="Main flow"]')
                flow.locator("#enter-visualize-mode").click()
                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    opened = call(portal, client, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(opened.is_error, opened.content)
                    owner = output(opened)["edit_lock"]["owner"]
                    for label, path, scope in (
                        ("Workflow dashboard", "dashboards/workflow", "workflow"),
                        ("Multiply by Ten dashboard", "dashboards/nodes/Multiply by Ten", "1"),
                    ):
                        with self.subTest(scope=scope):
                            panel = flow.get_by_role("region", name=label, exact=True)
                            expect(panel.locator("iframe")).to_be_visible()
                            endpoint = f"{workspace}flows/0/view/dashboard-source/{scope}"
                            read = page.request.post(endpoint, headers={"Origin": url}, data={
                                "action": "read", "panel": "default", "name": "index.html",
                            })
                            self.assertEqual(read.status, 200, read.text())
                            original = (root / path / "index.html").read_text(encoding="utf-8")
                            self.assertEqual(read.json()["source"], original)
                            for mutation in (
                                {"action": "create", "panel": "new-panel"},
                                {"action": "remove", "panel": "default"},
                                {"action": "save", "panel": "default", "name": "index.html",
                                 "revision": read.json()["revision"], "source": "<h1>New dashboard</h1>"},
                            ):
                                result = page.request.post(endpoint, headers={"Origin": url}, data=mutation)
                                self.assertEqual(result.status, 409, result.text())
                                self.assertEqual(result.json()["edit_lock"]["owner"], owner)
                            self.assertEqual((root / path / "index.html").read_text(encoding="utf-8"), original)
                            self.assertFalse((root / path / "panels/new-panel").exists())
                            panel.get_by_role("button", name="Edit dashboard").click()
                            editor = flow.locator("#dashboard-editor")
                            source = editor.get_by_role("textbox", name="Source code")
                            expect(source).to_have_value(original)
                            expect(source).not_to_be_editable()
                            expect(editor.get_by_role("status")).to_contain_text(owner)
                            expect(editor.get_by_role("button", name="Save", exact=True)).to_be_disabled()
                            editor.get_by_role("button", name="Close dashboard editor").click()

    def test_browser_dashboard_editor_holds_a_sheet_lease_and_checks_revisions(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "BrowserDashboardLocks"
            state = parent / "state"
            with application(state) as url:
                workspace_id, token = create_workspace_and_token(page, url, state, root)
                workspace = f"{url}/workspaces/{workspace_id}/"
                created = page.request.post(f"{workspace}flows", headers={"Origin": url}, data={"name": "Other"})
                self.assertEqual(created.status, 201, created.text())
                page.goto(workspace)
                flow = page.frame_locator('iframe[title="Main flow"]')
                editor = flow.locator("#dashboard-editor")
                panel_id = editor.get_by_label("Panel ID")
                create = editor.get_by_role("button", name="Create dashboard")

                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    for scope, label, directory in (
                        ("Workflow", "Workflow dashboard", "dashboards/workflow"),
                        ("Start", "Start dashboard", "dashboards/nodes/Start"),
                    ):
                        with self.subTest(scope=scope):
                            flow.get_by_role("button", name="Add dashboard").click()
                            editor.get_by_label("Scope").select_option(label=scope)
                            panel_id.fill("test-status")
                            expect(create).to_be_enabled()
                            denied = call(portal, client, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                            self.assertTrue(denied.is_error)
                            self.assertIn("Web editor", denied.content[0].text)

                            other = call(portal, client, "open_sheet", workspace_id=workspace_id, sheet="Other", mode="edit")
                            self.assertFalse(other.is_error, other.content)
                            other_session = output(other)["session_id"]
                            other_write = call(
                                portal, client, "write_file", workspace_id=workspace_id, sheet="Other",
                                session_id=other_session, path=f"{directory}/panels/test-status/index.html",
                                revision="missing", source="<h1>Other dashboard</h1>",
                            )
                            self.assertFalse(other_write.is_error, other_write.content)
                            closed = call(portal, client, "close_sheet", workspace_id=workspace_id, sheet="Other",
                                          session_id=other_session)
                            self.assertFalse(closed.is_error, closed.content)

                            with page.expect_response(lambda response: "/dashboard-source/" in response.url) as result:
                                create.click()
                            self.assertEqual(result.value.status, 200, result.value.text())
                            panel = flow.get_by_role("region", name=f"{label} · test status", exact=True)
                            preview = panel.frame_locator("iframe")
                            expect(preview.get_by_role("heading", name="Dashboard")).to_be_visible()
                            panel.get_by_role("button", name="Edit dashboard").click()
                            source = editor.get_by_role("textbox", name="Source code")
                            expect(source).to_be_editable()
                            relative = f"{directory}/panels/test-status/index.html"
                            read = call(portal, client, "read_workspace_text", workspace_id=workspace_id, path=relative)
                            self.assertFalse(read.is_error, read.content)
                            denied_write = call(
                                portal, client, "write_workspace_text", workspace_id=workspace_id, path=relative,
                                revision=output(read)["revision"], text="<h1>MCP dashboard</h1>",
                            )
                            self.assertTrue(denied_write.is_error)
                            self.assertIn("Web editor", denied_write.content[0].text)
                            read = call(portal, client, "read_file", workspace_id=workspace_id, sheet="Main", path=relative)
                            self.assertFalse(read.is_error, read.content)
                            denied_write = call(
                                portal, client, "write_file", workspace_id=workspace_id, sheet="Main",
                                path=relative, revision=output(read)["revision"], source="<h1>MCP dashboard</h1>",
                            )
                            self.assertTrue(denied_write.is_error)
                            self.assertIn("Web editor", denied_write.content[0].text)
                            source.fill("<h1>Saved dashboard</h1>")
                            save = editor.get_by_role("button", name="Save", exact=True)
                            save.click()
                            expect(editor.get_by_role("status")).to_have_text("Saved.")
                            expect(preview.get_by_role("heading", name="Saved dashboard")).to_have_count(1)
                            asset = root / relative
                            self.assertEqual(asset.read_text(encoding="utf-8"), "<h1>Saved dashboard</h1>")
                            asset.write_text("<h1>Newer dashboard</h1>", encoding="utf-8")
                            source.fill("<h1>Stale dashboard</h1>")
                            save.click()
                            expect(editor.get_by_role("status")).to_contain_text("changed on disk")
                            self.assertEqual(asset.read_text(encoding="utf-8"), "<h1>Newer dashboard</h1>")
                            page.once("dialog", lambda confirm: confirm.accept())
                            editor.get_by_role("button", name="Close dashboard editor").click()
                            panel.get_by_role("button", name="Remove dashboard").click()
                            editor.get_by_role("button", name="Remove dashboard", exact=True).click()
                            expect(panel).to_have_count(0)
                            self.assertFalse(asset.exists())
                            opened = wait_for_open_sheet(portal, client, workspace_id)
                            self.assertFalse(opened.is_error, opened.content)
                            closed = call(portal, client, "close_sheet", workspace_id=workspace_id,
                                          sheet="Main", session_id=output(opened)["session_id"])
                            self.assertFalse(closed.is_error, closed.content)

    def test_dashboard_editor_creates_edits_and_removes_panel_assets(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "DashboardEditor"
            state = parent / "state"
            copytree(LIVE_COUNTER, root, ignore=ignore_patterns(".workspace", "__pycache__", ".venv"))
            with application(state) as url:
                sign_in(page, url, state)
                added = page.request.post(
                    f"{url}/workspaces", headers={"Origin": url}, data={"path": str(root), "action": "add"},
                )
                self.assertEqual(added.status, 201, added.text())
                workspace_url = f"{url}/workspaces/{added.json()['id']}"
                page.goto(f"{workspace_url}/")
                flow = page.frame_locator('iframe[title="Main flow"]')
                flow.get_by_role("button", name="Add dashboard").click()
                editor = flow.locator("#dashboard-editor")
                editor.get_by_label("Scope").select_option(label="Workflow")
                editor.get_by_label("Panel ID").fill("run-status")
                editor.get_by_role("button", name="Create dashboard").click()
                directory = root / "dashboards/workflow/panels/run-status"
                panel = flow.get_by_role("region", name="Workflow dashboard · run status", exact=True)
                frame = panel.frame_locator("iframe")
                expect(frame.get_by_role("heading", name="Dashboard")).to_be_visible()
                self.assertTrue((directory / "bridge.json").is_file())

                flow.get_by_role("button", name="Add dashboard").click()
                editor.get_by_label("Panel ID").fill("run-status")
                editor.get_by_role("button", name="Create dashboard").click()
                expect(editor.get_by_role("status")).to_contain_text("already exists")
                editor.get_by_role("button", name="Close dashboard editor").click()

                panel.get_by_role("button", name="Edit dashboard").click()
                source = editor.get_by_role("textbox", name="Source code")
                expect(source).to_have_value((directory / "index.html").read_text(encoding="utf-8"))
                editor.get_by_label("Source file").select_option("dashboard.js")
                expect(source).to_have_value((directory / "dashboard.js").read_text(encoding="utf-8"))
                editor.get_by_label("Source file").select_option("index.html")
                expect(source).to_have_value((directory / "index.html").read_text(encoding="utf-8"))
                markup = '<h1>Edited dashboard</h1><p role="status"></p>'
                source.fill(markup)
                editor.get_by_role("button", name="Save", exact=True).click()
                expect(editor.get_by_role("status")).to_have_text("Saved.")
                expect(frame.get_by_role("heading", name="Edited dashboard")).to_have_count(1)
                self.assertEqual((directory / "index.html").read_text(encoding="utf-8"), markup)
                editor.get_by_role("button", name="Close dashboard editor").click()

                panel.get_by_role("button", name="Remove dashboard").click()
                editor.get_by_role("button", name="Close dashboard editor").click()
                self.assertTrue((directory / "index.html").is_file())
                panel.get_by_role("button", name="Remove dashboard").click()
                editor.get_by_role("button", name="Remove dashboard", exact=True).click()
                expect(panel).to_have_count(0)
                self.assertFalse(directory.exists())

                flow.get_by_role("button", name="Add dashboard").click()
                editor = flow.locator("#dashboard-editor")
                editor.get_by_label("Scope").select_option(label="Multiply by Ten")
                editor.get_by_label("Panel ID").fill("node-status")
                editor.get_by_role("button", name="Create dashboard").click()
                node_panel = flow.get_by_role("region", name="Multiply by Ten dashboard · node status")
                expect(node_panel.frame_locator("iframe").get_by_role("heading", name="Dashboard")).to_be_visible()
                node_dir = root / "dashboards/nodes/Multiply by Ten/panels/node-status"
                self.assertTrue((node_dir / "index.html").is_file())
                node_panel.get_by_role("button", name="Remove dashboard").click()
                editor.get_by_role("button", name="Remove dashboard", exact=True).click()
                expect(node_panel).to_have_count(0)
                self.assertFalse(node_dir.exists())

                flow.get_by_role("button", name="Add dashboard").click()
                editor.get_by_label("Scope").select_option(label="Workflow")
                editor.get_by_label("Panel ID").fill("workflow-status")
                editor.get_by_role("button", name="Create dashboard").click()
                workflow = flow.get_by_role("region", name="Workflow dashboard · workflow status")
                expect(workflow.frame_locator("iframe").get_by_role("heading", name="Dashboard")).to_be_visible()
                self.assertTrue((root / "dashboards/workflow/panels/workflow-status/index.html").is_file())

    def test_visualize_layout_is_shared_per_sheet_and_fits_narrow_screens(self):
        with TemporaryDirectory() as temp, browser_page(show_scrollbars=True) as page:
            parent = Path(temp).resolve()
            root = parent / "PanelLayouts"
            state = parent / "state"
            ignore = ignore_patterns(".workspace", "__pycache__", ".venv")
            copytree(LIVE_COUNTER, root, ignore=ignore)
            node = root / "dashboards/nodes/Multiply by Ten"
            (node / "panels/details").mkdir(parents=True)
            (node / "index.html").rename(node / "panels/details/index.html")
            (node / "panels/details/dashboard.js").write_text(
                "document.querySelector('button').onclick = event => { event.target.textContent = 'Checked'; };",
                encoding="utf-8",
            )
            with (node / "panels/details/index.html").open("a", encoding="utf-8") as markup:
                markup.write("<button>Check dashboard</button>")
            copytree(LIVE_COUNTER, root / "flows/Other", ignore=ignore)

            def enter(viewer, workspace):
                viewer.goto(workspace)
                flow = viewer.frame_locator('iframe[title="Main flow"]')
                flow.locator("#enter-visualize-mode").click()
                expect(flow.locator("body")).to_have_class("is-visualize-mode")
                expect(flow.locator(".graph-scroll")).not_to_have_attribute("data-layout-locked", "")
                return flow

            def bounds(flow):
                return flow.locator(".dashboard-panel:not([hidden])").evaluate_all(
                    """panels => panels.map(({ style }) =>
                        Object.fromEntries(['left', 'top', 'width', 'height'].map(key => [key, parseInt(style[key], 10)]))
                    )"""
                )

            def drag(handle, dx, dy):
                handle.scroll_into_view_if_needed()
                box = handle.bounding_box()
                x = box["x"] + box["width"] / 2
                y = box["y"] + box["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + dx, y + dy, steps=4)
                page.mouse.up()

            with application(state) as url:
                sign_in(page, url, state)
                added = page.request.post(
                    f"{url}/workspaces", headers={"Origin": url}, data={"path": str(root), "action": "add"},
                )
                self.assertEqual(added.status, 201, added.text())
                workspace_id = added.json()["id"]
                workspace = f"{url}/workspaces/{workspace_id}/"
                endpoint = f"{workspace}flows/0/view/visualize-layout"
                flow = enter(page, workspace)
                panels = flow.locator(".dashboard-panel:not([hidden])")
                expect(panels).to_have_count(2)
                before = bounds(flow)

                with page.expect_response(
                    lambda response: response.url.endswith("/visualize-layout") and response.request.method == "POST",
                ) as moved:
                    drag(panels.nth(0).locator('[data-dash="handle"]'), 100, 40)
                self.assertEqual(moved.value.status, 200)
                after_move = bounds(flow)
                self.assertEqual(after_move[0]["left"], before[0]["left"] + 100)
                self.assertEqual(after_move[0]["top"], before[0]["top"] + 40)
                self.assertGreater(after_move[1]["top"], before[1]["top"])

                page.route("**/visualize-layout", lambda route: route.fulfill(status=503))
                drag(panels.nth(1).locator('[data-dash="resize"]'), -80, 40)
                status = flow.locator("#visualize-layout-status")
                expect(status.get_by_role("status")).to_have_text("Could not save the dashboard layout.")
                saved = bounds(flow)
                self.assertEqual(saved[1]["width"], after_move[1]["width"] - 80)
                self.assertEqual(saved[1]["height"], after_move[1]["height"] + 40)
                page.unroute("**/visualize-layout")
                status.get_by_role("button", name="Retry").click()
                expect(status).to_be_hidden()
                with page.expect_response(
                    lambda response: response.url.endswith("/visualize-layout") and response.request.method == "POST",
                ) as resized:
                    panels.nth(1).locator('[data-dash="handle"]').press("Shift+ArrowDown")
                self.assertEqual(resized.value.status, 200)
                self.assertEqual(bounds(flow)[1]["height"], saved[1]["height"] + 20)
                saved = bounds(flow)

                keys = [
                    json.dumps(identity, separators=(",", ":"))
                    for identity in ([None, "default"], ["Multiply by Ten", "details"])
                ]
                expected = {"panels": dict(zip(keys, saved))}
                self.assertEqual(page.request.get(endpoint).json(), expected)
                flow.locator("#exit-visualize-mode").click()
                flow.locator("#enter-visualize-mode").click()
                expect(flow.locator(".graph-scroll")).not_to_have_attribute("data-layout-locked", "")
                self.assertEqual(bounds(flow), saved)
                flow = enter(page, workspace)
                self.assertEqual(bounds(flow), saved)

                page.set_viewport_size({"width": 390, "height": 800})
                viewport = flow.locator(".graph-scroll")
                expect(viewport).to_have_attribute("data-layout-fitted", "")
                fitted = bounds(flow)
                width, scroll_width = viewport.evaluate("el => [el.clientWidth, el.scrollWidth]")
                self.assertEqual(width, scroll_width)
                self.assertEqual(fitted[1]["left"], fitted[0]["left"])
                self.assertGreaterEqual(fitted[1]["top"], fitted[0]["top"] + fitted[0]["height"])
                for rendered, original in zip(fitted, saved):
                    self.assertLessEqual(rendered["left"] + rendered["width"], width)
                    self.assertEqual(rendered["height"], original["height"])
                dashboard = flow.locator(".dashboard-panel:not([hidden])").nth(1).locator("iframe").content_frame
                dashboard.get_by_role("button", name="Check dashboard").click()
                expect(dashboard.get_by_role("button", name="Checked")).to_be_visible()
                page.set_viewport_size({"width": 1440, "height": 900})
                expect(viewport).not_to_have_attribute("data-layout-fitted", "")
                self.assertEqual(bounds(flow), saved)
                self.assertEqual(page.request.get(endpoint).json(), expected)

                page.route("**/visualize-layout", lambda route: route.fulfill(status=503))
                flow.locator("#exit-visualize-mode").click()
                flow.locator("#enter-visualize-mode").click()
                expect(flow.locator("#visualize-layout-status [role='status']")).to_have_text("Could not load the dashboard layout.")
                expect(viewport).to_have_attribute("data-layout-locked", "")
                page.unroute("**/visualize-layout")
                flow.locator("#visualize-layout-status").get_by_role("button", name="Retry").click()
                expect(viewport).not_to_have_attribute("data-layout-locked", "")
                self.assertEqual(bounds(flow), saved)

                second = page.context.browser.new_page(viewport={"width": 1440, "height": 900})
                try:
                    self.assertEqual(second.request.get(endpoint).status, 401)
                    sign_in(second, url, state)
                    self.assertEqual(bounds(enter(second, workspace)), saved)
                finally:
                    second.close()

                headers = {"Origin": url}
                invalid = {"panels": {keys[0]: {**saved[0], "left": 200}, keys[1]: {**saved[1], "width": True}}}
                self.assertEqual(page.request.post(endpoint, headers=headers, data=invalid).status, 400)
                self.assertEqual(page.request.get(endpoint).json(), expected)
                other = f"{workspace}flows/1/view/visualize-layout"
                self.assertEqual(page.request.get(other).json(), {"panels": {}})
                other_layout = {"panels": {keys[0]: {**saved[0], "left": 300}}}
                self.assertEqual(page.request.post(other, headers=headers, data=other_layout).status, 200)
                self.assertEqual(page.request.get(other).json(), other_layout)
                self.assertEqual(page.request.get(endpoint).json(), expected)
                self.assertEqual(page.request.post(f"{workspace}flows/1/delete", headers=headers).status, 200)
                created = page.request.post(f"{workspace}flows", headers=headers, data={"name": "Other"})
                self.assertEqual(created.status, 201)
                self.assertEqual(
                    page.request.get(f"{workspace}flows/{created.json()['index']}/view/visualize-layout").json(),
                    {"panels": {}},
                )

            with application(state) as url:
                sign_in(page, url, state)
                self.assertEqual(bounds(enter(page, f"{url}/workspaces/{workspace_id}/")), saved)

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
                    tools = {tool.name: tool for tool in portal.call(client.list_tools).tools}
                    self.assertIn("Pass sheet: null for the Workspace Dashboard", tools["inspect_dashboards"].description)
                    instructions = client.instructions or ""
                    self.assertIn("inspect_dashboards with sheet: null", instructions)
                    self.assertIn("read_workspace_text and write_workspace_text", instructions)
                    self.assertIn("Use a browser for visual checks only if the user explicitly asks", instructions)

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
                    resize_guidance = info["theme_reference"]["resize_guidance"]
                    self.assertIn("its own available width and height", resize_guidance)
                    self.assertIn("keeping the browser window size fixed", resize_guidance)
                    self.assertIn(resize_guidance, instructions)
                    self.assertIn("If the user explicitly requests browser verification", resize_guidance)

                    def inspect_workspace_panels():
                        inspected = call(portal, client, "inspect_dashboards", workspace_id=workspace_id, sheet=None)
                        self.assertFalse(inspected.is_error, inspected.content)
                        data = output(inspected)
                        self.assertNotIn("workflow", data)
                        self.assertNotIn("nodes", data)
                        return data["workspace"]["panels"]

                    self.assertEqual(inspect_workspace_panels(), [])
                    workspace_asset = {"workspace_id": workspace_id, "path": "dashboards/workspace/index.html"}
                    wrote = call(portal, client, "write_workspace_text", **workspace_asset,
                                 revision="missing", text="<h1>Workspace overview</h1>")
                    self.assertFalse(wrote.is_error, wrote.content)
                    saved = call(portal, client, "read_workspace_text", **workspace_asset)
                    self.assertFalse(saved.is_error, saved.content)
                    edited = call(portal, client, "write_workspace_text", **workspace_asset,
                                  revision=output(saved)["revision"], text="<h1>Workspace status</h1>")
                    self.assertFalse(edited.is_error, edited.content)
                    self.assertEqual(output(call(portal, client, "read_workspace_text", **workspace_asset))["text"],
                                     "<h1>Workspace status</h1>")
                    panels = inspect_workspace_panels()
                    self.assertEqual(len(panels), 1)
                    self.assertEqual(panels[0]["assets"], [workspace_asset["path"]])
                    self.assertTrue(panels[0]["ready"])

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

                page.set_viewport_size({"width": 620, "height": 900})
                page.goto(f"{url}/workspaces/{workspace_id}/")
                flow = page.frame_locator('iframe[title="Main flow"]')
                expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()
                expect(flow.locator("#panel-visualize")).to_be_hidden()
                page.set_viewport_size({"width": 1440, "height": 900})
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

        theme_css = (STATIC / "dashboard-bridge.js").read_text(encoding="utf-8")
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
                        ("dashboards/workspace/index.html", "<h1>Workspace metrics</h1>\n"),
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
                    workspace_panels = output(call(portal, client, "inspect_dashboards", workspace_id=workspace_id, sheet=None))
                    self.assertEqual(workspace_panels["workspace"], inspected["workspace"])
                    self.assertEqual(workspace_panels["workspace"]["panels"][0]["assets"], ["dashboards/workspace/index.html"])
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

    def test_sheet_dashboard_identity_matches_mcp_and_flow_editor(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "PanelCatalog"
            other = root / "flows/Other"
            state = parent / "state"
            for sheet in (root, other):
                copytree(LIVE_COUNTER, sheet, ignore=ignore_patterns(".workspace", "__pycache__", ".venv"))
            node_id = "Multiply by Ten"
            workflow = "dashboards/workflow"
            node = f"dashboards/nodes/{node_id}"
            for sheet, path, heading in (
                (root, f"{workflow}/index.html", "Workflow overview"),
                (root, f"{workflow}/panels/default/index.html", "Reserved workflow collision"),
                (root, f"{workflow}/panels/shared/index.html", "Main workflow panel"),
                (root, f"{node}/panels/default/index.html", "Reserved node collision"),
                (root, f"{node}/panels/shared/index.html", "Main node panel"),
                (root, "dashboards/nodes/%4Dultiply by Ten/panels/alias/index.html", "Noncanonical node"),
                (other, f"{workflow}/panels/shared/index.html", "Other workflow panel"),
            ):
                asset = sheet / path
                asset.parent.mkdir(parents=True, exist_ok=True)
                asset.write_text(f"<h1>{heading}</h1>", encoding="utf-8")
            incomplete = root / workflow / "panels/incomplete"
            incomplete.mkdir()
            (incomplete / "style.css").write_text("main { color: red; }", encoding="utf-8")
            notes = root / workflow / "README.md"
            notes.write_text("Dashboard notes", encoding="utf-8")

            with application(state) as url:
                sign_in(page, url, state)
                added = page.request.post(
                    f"{url}/workspaces", headers={"Origin": url}, data={"path": str(root), "action": "add"},
                )
                self.assertEqual(added.status, 201, added.text())
                workspace_id = added.json()["id"]
                token = issue_token(page, url, "Inspector", [workspace_id])
                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    def inspect(sheet):
                        return output(call(portal, client, "inspect_dashboards", workspace_id=workspace_id, sheet=sheet))

                    main = inspect("Main")
                    remote = inspect("Other")
                    self.assertEqual([(p["id"], p["ready"]) for p in main["workflow"]], [
                        ("default", True), ("incomplete", False), ("shared", True),
                    ])
                    self.assertEqual([group["step_id"] for group in main["nodes"]], [node_id])
                    self.assertEqual([p["id"] for p in main["nodes"][0]["panels"]], ["default", "shared"])
                    self.assertEqual(main["nodes"][0]["panels"][1]["assets"], [f"{node}/panels/shared/index.html"])
                    self.assertEqual(remote["workflow"][1]["assets"], [f"{workflow}/panels/shared/index.html"])
                    self.assertEqual((main["sheet_path"], remote["sheet_path"]), (".", "flows/Other"))

                    workspace_url = f"{url}/workspaces/{workspace_id}"
                    catalog_url = f"{workspace_url}/dashboard/panel-catalog"
                    response = page.request.get(catalog_url)
                    self.assertEqual(response.status, 200, response.text())
                    catalog = response.json()["panels"]
                    self.assertCountEqual(
                        [(p["sheet"], p["sheet_name"], p["scope"], p["label"]) for p in catalog if p["id"] == "shared"],
                        [(".", "Main", None, "shared"), (".", "Main", node_id, "shared"),
                         ("flows/Other", "Other", None, "shared")],
                    )
                    self.assertFalse({"incomplete", "alias"} & {p["id"] for p in catalog})

                    page.goto(f"{workspace_url}/")
                    flow = page.frame_locator('iframe[title="Main flow"]')
                    overview_button = flow.locator('[data-vis-node=""][data-vis-panel="default"]')
                    expect(overview_button).to_have_count(1)
                    expect(flow.locator('[data-vis-panel="incomplete"]')).to_have_count(0)

                    for scope, region, heading in (
                        ("", "Workflow dashboard · shared", "Main workflow panel"),
                        (node_id, f"{node_id} dashboard · shared", "Main node panel"),
                    ):
                        flow.locator(f'[data-vis-node="{scope}"][data-vis-panel="shared"]').click()
                        board = flow.get_by_role("region", name=region, exact=True)
                        expect(board.frame_locator("iframe").get_by_role("heading", name=heading)).to_be_visible()

                    overview_button.click()
                    overview = flow.get_by_role("region", name="Workflow dashboard", exact=True)
                    expect(overview.frame_locator("iframe").get_by_role("heading", name="Workflow overview")).to_be_visible()
                    overview.get_by_role("button", name="Remove dashboard").click()
                    flow.locator("#dashboard-editor").get_by_role("button", name="Remove dashboard", exact=True).click()
                    expect(overview).to_have_count(0)
                    expect(overview_button).to_have_count(0)
                    self.assertEqual([panel["id"] for panel in inspect("Main")["workflow"]], ["incomplete", "shared"])
                    self.assertFalse((root / workflow / "index.html").exists())
                    self.assertTrue((root / workflow / "panels/default/index.html").is_file())
                    self.assertTrue(notes.is_file())
                    expect(flow.get_by_role("heading", name="Workflow dashboard · shared")).to_be_visible()
                    self.assertFalse(any(p["sheet"] == "." and p["scope"] is None and p["id"] == "default"
                                         for p in page.request.get(catalog_url).json()["panels"]))

                    page.get_by_role("tab", name="Other").click()
                    flow = page.frame_locator('iframe[title="Other flow"]')
                    flow.locator('[data-vis-node=""][data-vis-panel="shared"]').click()
                    expect(flow.frame_locator("#dashboard-preview iframe").get_by_role(
                        "heading", name="Other workflow panel",
                    )).to_be_visible()

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
