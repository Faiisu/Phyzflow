import unittest
from base64 import b64decode
from pathlib import Path
from tempfile import TemporaryDirectory

from browser_support import browser_page
from playwright.sync_api import expect
from test_application import application
from test_application_lifecycle import create_workspace, wait_for
from workspace_sdk.sheets import create_sheet

CAMERA_FLOW = '''from pathlib import Path
from time import sleep

from workspace_sdk import Flow, Output, Step, publish_frame, publish_metrics, publish_output, record_metrics, record_reason

root = Path(__file__).parent


def other_camera():
    publish_frame((root / "other.jpg").read_bytes())
    record_metrics("Sample", {"Value": 999})


def camera():
    frame = (root / "camera.jpg").read_bytes()
    print("private-console")
    for _ in range(500):
        value = int((root / "value").read_text())
        publish_output({"preview": "private-preview"})
        publish_frame(frame)
        publish_metrics("Sample", {"Value": value})
        if (root / "finish").exists():
            record_reason("private-reason")
            record_metrics("Sample", {"Value": value})
            return "private-output"
        sleep(.05)
    raise TimeoutError("The test did not release the camera.")


flow = Flow((), (Step("Other Camera", other_camera, {}), Step("Live Camera", camera, {}, capture_samples=True)), Output("Live Camera"))
'''


def write_panel(folder, version=None):
    folder.mkdir(parents=True)
    (folder / "index.html").write_text(
        '<output id="reading">No update</output><img alt="Live camera" hidden>', encoding="utf-8",
    )
    (folder / "dashboard.js").write_text(
        """workspaceDashboard.subscribe(snapshot => {
  const {run, sheet, mode, timestamp, liveFrames, liveMetrics} = snapshot;
  const recorded = run?.steps.find(step => step.id === "Live Camera")?.metrics?.Sample?.Value;
  const value = liveMetrics["Live Camera"]?.Sample?.Value ?? recorded;
  const reading = document.querySelector("#reading");
  reading.value = run ? [sheet, mode, run.id, timestamp, value].join(" | ") : "Waiting";
  const image = document.querySelector("img");
  const frame = liveFrames["Live Camera"];
  image.hidden = !frame;
  if (frame) image.src = frame;
  else image.removeAttribute("src");
});""", encoding="utf-8",
    )
    if version is not None:
        (folder / "bridge.json").write_text(f'{{"versions": [{version}]}}', encoding="utf-8")


class WorkspaceDashboardTests(unittest.TestCase):
    def test_live_placement_distinguishes_stopped_and_skipped_steps(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            state, root = parent / "state", parent / "workspace"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                (root / "flow.py").write_text("""from pathlib import Path
from time import monotonic, sleep

from workspace_sdk import Flow, Output, Step

root = Path(__file__).parent


def unused():
    yield 1


def closing():
    try:
        yield 2
    finally:
        (root / "closing").touch()
        deadline = monotonic() + 20
        while monotonic() < deadline and not (root / "release").exists():
            sleep(.02)


def fail():
    raise ValueError("private-failure")


flow = Flow((), (
    Step("Unused", unused, {}, track_generator=True),
    Step("Closing", closing, {}, track_generator=True),
    Step("Failed", fail, {}),
    Step("Skipped", lambda value: value, {"value": Output("Failed")}),
    Step("Consume", lambda iterator: next(iterator), {"iterator": Output("Closing")}),
), Output("Consume"))
""", encoding="utf-8")
                write_panel(root / "dashboards/workflow")
                workspace.click()
                workspace_url = page.url.rstrip("/")
                response = page.request.post(
                    f"{workspace_url}/dashboard/placements", headers={"Origin": url},
                    data={"action": "add", "sheet": ".", "scope": None, "id": "default"},
                )
                self.assertTrue(response.ok, response.text())
                placement_id = response.json()["placements"][0]["id"]
                data_url = f"{workspace_url}/dashboard/placement-assets/{placement_id}/data"

                def snapshot():
                    response = page.request.get(data_url)
                    self.assertTrue(response.ok, response.text())
                    self.assertNotIn("private-failure", response.text())
                    return response.json()

                page.get_by_role("button", name="Run flow", exact=True).click()
                try:
                    wait_for(lambda: (root / "closing").is_file())
                    data = snapshot()
                    self.assertEqual(data["mode"], "live")
                    steps = {step["id"]: step for step in data["run"]["steps"]}
                    self.assertEqual(steps["Skipped"], {"id": "Skipped", "status": "skipped"})
                    self.assertEqual(steps["Unused"]["status"], "stopped")
                    self.assertTrue(steps["Unused"]["finished_at"])
                finally:
                    (root / "release").touch()
                wait_for(lambda: snapshot()["mode"] == "saved")
                saved = {step["id"]: step for step in snapshot()["run"]["steps"]}
                self.assertEqual(saved["Skipped"], {**steps["Skipped"], "metrics": {}})
                self.assertEqual(saved["Unused"], {**steps["Unused"], "metrics": {}})

    def test_workspace_placements_follow_unopened_sheets_and_show_saved_runs(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            state, root = parent / "state", parent / "workspace"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                first_sheet = create_sheet(root, "First Camera").parent
                second = create_sheet(root, "Second Camera").parent
                images = page.evaluate('''() => ["#ff0000", "#00ff00", "#0000ff"].map(color => {
                    const image = document.createElement("canvas");
                    image.width = image.height = 8;
                    const context = image.getContext("2d");
                    context.fillStyle = color;
                    context.fillRect(0, 0, 8, 8);
                    return image.toDataURL("image/jpeg");
                })''')
                for sheet, image, value in zip((first_sheet, second), images[:2], (12, 34), strict=True):
                    (sheet / "flow.py").write_text(CAMERA_FLOW, encoding="utf-8")
                    (sheet / "camera.jpg").write_bytes(b64decode(image.split(",")[1]))
                    (sheet / "other.jpg").write_bytes(b64decode(images[2].split(",")[1]))
                    (sheet / "value").write_text(str(value), encoding="utf-8")
                write_panel(first_sheet / "dashboards/workflow", version=1)
                write_panel(second / "dashboards/nodes/Live Camera")
                write_panel(first_sheet / "dashboards/workflow/panels/incompatible", version=2)

                workspace.click()
                workspace_url = page.url.rstrip("/")
                dashboard = page.get_by_role("button", name="Dashboard", exact=True)
                dialog = page.get_by_role("dialog", name="Workspace dashboard")
                dashboard.click()
                board = dialog.frame_locator('iframe[title="Workspace Dashboard"]')
                board.get_by_text("Panels", exact=True).click()
                titles = (
                    "First Camera · Workflow · Overview",
                    "Second Camera · Live Camera · Overview",
                    "First Camera · Workflow · incompatible",
                )
                for title in titles:
                    board.get_by_role("button", name=f"Add {title}", exact=True).click()
                board.get_by_text("Panels", exact=True).click()

                def region(title):
                    return board.get_by_role("region", name=title, exact=True)

                first, last = [region(title).frame_locator("iframe") for title in titles[:2]]
                expect(region(titles[2]).get_by_text(
                    "Incompatible Dashboard Bridge. Observed version 1. Supported versions 2.", exact=True,
                )).to_be_visible()
                expect(first.locator("output")).to_have_text("Waiting")
                expect(last.locator("output")).to_have_text("Waiting")
                self.assertEqual(first.locator("html").evaluate("() => typeof workspaceDashboard.setSetting"), "undefined")
                expect(page.locator('iframe[title$="Camera flow"]')).to_have_count(0)
                first_path = "flows/First Camera"
                second_path = "flows/Second Camera"
                workspace_state = page.request.get(f"{workspace_url}/workspace").json()
                for flow in workspace_state["flows"]:
                    if flow["name"].endswith("Camera"):
                        self.assertFalse(flow["worker_ready"])
                placements = page.request.get(f"{workspace_url}/dashboard/placements").json()["placements"]
                endpoints = {
                    placement["sheet"]: f"{workspace_url}/dashboard/placement-assets/{placement['id']}/data"
                    for placement in placements if placement["panel_id"] == "default"
                }

                def snapshot(sheet):
                    response = page.request.get(endpoints[sheet])
                    self.assertTrue(response.ok, response.text())
                    for private in ("private-console", "private-preview", "private-reason", "private-output"):
                        self.assertNotIn(private, response.text())
                    return response.json()

                def command(path):
                    return page.request.post(f"{workspace_url}/{path}", headers={"Origin": url})

                with page.context.browser.new_context() as anonymous:
                    self.assertEqual(anonymous.request.get(endpoints[first_path]).status, 401)
                self.assertEqual(command("run-all").status, 202)
                active = {}
                for index, (sheet, frame, value) in enumerate(zip((first_path, second_path), (first, last), (12, 34), strict=True)):
                    expect(frame.locator("img")).to_have_attribute("src", images[index], timeout=20_000)
                    self.assertTrue(frame.locator("img").evaluate("image => image.naturalWidth === 8"))
                    data = active[sheet] = snapshot(sheet)
                    self.assertEqual(data["sheet"], sheet)
                    self.assertEqual(data["mode"], "live")
                    self.assertEqual(data["timestamp"], data["run"]["started_at"])
                    self.assertEqual(data["run"]["status"], "running")
                    expect(frame.locator("output")).to_have_text(
                        f"{sheet} | live | {data['run']['id']} | {data['timestamp']} | {value}",
                    )
                    self.assertEqual(data["previews"], {})
                    self.assertEqual(data["settings"], {})
                self.assertNotEqual(active[first_path]["run"]["id"], active[second_path]["run"]["id"])
                node = active[second_path]
                self.assertEqual(node["nodeId"], "Live Camera")
                self.assertEqual([item["id"] for item in node["graph"]], ["Live Camera"])
                self.assertEqual([step["id"] for step in node["run"]["steps"]], ["Live Camera"])
                self.assertEqual(node["run"]["steps"][0]["status"], "running")
                self.assertTrue(node["run"]["steps"][0]["started_at"])
                self.assertEqual(list(node["liveMetrics"]), ["Live Camera"])
                self.assertEqual(list(node["liveFrames"]), ["Live Camera"])
                self.assertEqual(active[first_path]["liveFrames"]["Other Camera"], images[2])
                expect(page.locator('iframe[title$="Camera flow"]')).to_have_count(0)

                renamed = page.request.post(
                    f"{workspace_url}/flows/2/rename", headers={"Origin": url}, data={"name": "Renamed Camera"},
                )
                self.assertTrue(renamed.ok, renamed.text())
                board.get_by_text("Panels", exact=True).click()
                board.get_by_role("button", name="Refresh", exact=True).click()
                titles = (titles[0], "Renamed Camera · Live Camera · Overview", titles[2])
                last = region(titles[1]).frame_locator("iframe")
                expect(last.locator("img")).to_have_attribute("src", images[1])
                assert_renamed = snapshot(second_path)
                self.assertEqual(assert_renamed["sheetName"], "Renamed Camera")
                self.assertEqual(assert_renamed["run"]["id"], node["run"]["id"])
                board.get_by_text("Panels", exact=True).click()

                self.assertEqual(command("flows/2/stop").status, 200)
                expect(last.locator("img")).to_be_hidden()
                expect(first.locator("img")).to_have_attribute("src", images[0])
                next_value = first_sheet / "next-value"
                next_value.write_text("13", encoding="utf-8")
                next_value.replace(first_sheet / "value")
                expect(first.locator("output")).to_contain_text("| 13")
                (first_sheet / "finish").touch()
                wait_for(lambda: snapshot(first_path)["mode"] == "saved")
                saved = snapshot(first_path)
                saved_run = saved["run"]
                self.assertEqual(saved_run["id"], active[first_path]["run"]["id"])
                self.assertEqual(saved_run["status"], "succeeded")
                self.assertGreater(saved_run["duration_ms"], 0)
                self.assertRegex(saved_run["source_version"], r"^[0-9a-f]{64}$")
                self.assertEqual(saved["timestamp"], saved_run["finished_at"])
                self.assertEqual(saved["liveFrames"], {})
                self.assertEqual(saved["liveMetrics"], {})
                expect(first.locator("img")).to_be_hidden()
                expect(first.locator("output")).to_have_text(
                    f"{first_path} | saved | {saved_run['id']} | {saved['timestamp']} | 13",
                )
                record = first_sheet / ".workspace/runs" / f"{saved_run['id']}.json"
                self.assertTrue(record.is_file())
                self.assertNotIn(images[0].split(",")[1], record.read_text(encoding="utf-8"))

                (second / "finish").touch()
                self.assertEqual(command("flows/2/run").status, 202)
                wait_for(lambda: snapshot(second_path)["mode"] == "saved")
                saved_node = snapshot(second_path)
                self.assertNotEqual(saved_node["run"]["id"], node["run"]["id"])
                self.assertEqual([step["id"] for step in saved_node["run"]["steps"]], ["Live Camera"])
                self.assertEqual(saved_node["liveFrames"], {})
                expect(last.locator("output")).to_have_text(
                    f"{second_path} | saved | {saved_node['run']['id']} | {saved_node['timestamp']} | 34",
                )
                expect(last.locator("img")).to_be_hidden()

                (second / "dashboards/nodes/Live Camera/index.html").unlink()
                expect(region(titles[1]).get_by_text("Source panel unavailable.", exact=True)).to_be_visible()
                expect(first.locator("output")).to_contain_text(saved_run["id"])

                page.set_viewport_size({"width": 390, "height": 844})
                expect(board.locator(".graph-scroll")).to_have_attribute("data-layout-fitted", "")
                first_bounds, last_bounds = [region(title).bounding_box() for title in titles[:2]]
                self.assertEqual(dialog.bounding_box(), {"x": 0, "y": 0, "width": 390, "height": 844})
                self.assertEqual(first_bounds["x"], last_bounds["x"])
                self.assertGreaterEqual(last_bounds["y"], first_bounds["y"] + first_bounds["height"])
                self.assertLessEqual(last_bounds["x"] + last_bounds["width"], 390)
                self.assertTrue(first.locator("html").evaluate("root => root.scrollWidth === root.clientWidth"))
                board.get_by_role("button", name="Back to sheets").click()
                expect(dialog).to_be_hidden()
                expect(dashboard).to_be_focused()

    def test_replacing_and_closing_a_placement_ends_its_pending_update(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            state, root = parent / "state", parent / "workspace"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                workflow = root / "dashboards/workflow"
                for folder in (workflow, workflow / "panels/neighbor"):
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / "index.html").write_text("<output>No update</output>", encoding="utf-8")
                    (folder / "dashboard.js").write_text(
                        """const output = document.querySelector("output");
let count = 0;
workspaceDashboard.subscribe(snapshot => {
  output.value = snapshot.sheet;
  output.dataset.updates = String(++count);
});
parent.postMessage({ type: "dashboard-ready" }, "*");""", encoding="utf-8",
                    )
                workspace.click()
                placement_url = f"{page.url.rstrip('/')}/dashboard/placements"
                for panel_id in ("default", "neighbor"):
                    response = page.request.post(placement_url, headers={"Origin": url}, data={
                        "action": "add", "sheet": ".", "scope": None, "id": panel_id,
                    })
                    self.assertTrue(response.ok, response.text())
                target = next(item["id"] for item in response.json()["placements"] if item["panel_id"] == "default")
                page.add_init_script(f"""(() => {{
  if (!location.pathname.endsWith("/dashboard/")) return;
  const originalFetch = window.fetch;
  window.holdNext = true;
  window.attempts = 0;
  window.pending = [];
  window.fetch = async (path, options) => {{
    if (path !== "placement-assets/{target}/data") return originalFetch(path, options);
    window.attempts++;
    const held = window.holdNext;
    window.holdNext = false;
    const response = await originalFetch(path, options);
    if (!held) return response;
    const snapshot = await response.json();
    return new Promise(resolve => window.pending.push(superseded => {{
      if (superseded) snapshot.sheet = "Superseded";
      resolve(Response.json(snapshot));
    }}));
  }};
}})();""")
                page.get_by_role("button", name="Dashboard", exact=True).click()
                board = page.get_by_role("dialog", name="Workspace dashboard").frame_locator(
                    'iframe[title="Workspace Dashboard"]'
                )
                body = board.locator("body")
                panel = board.get_by_role("region", name="Main · Workflow · Overview", exact=True)
                output = panel.frame_locator("iframe").locator("output")
                neighbor = board.get_by_role("region", name="Main · Workflow · neighbor", exact=True)
                neighbor_output = neighbor.frame_locator("iframe").locator("output")

                def wait_for_neighbor_updates():
                    count = int(neighbor_output.get_attribute("data-updates"))
                    wait_for(lambda: int(neighbor_output.get_attribute("data-updates")) >= count + 2)

                wait_for(lambda: body.evaluate("() => window.pending.length") == 1)
                expect(output).to_have_text("No update")
                expect(neighbor_output).to_have_text(".")
                wait_for_neighbor_updates()
                self.assertEqual(body.evaluate("() => window.attempts"), 1)

                panel.get_by_role("button", name="Reload dashboard", exact=True).click()
                expect(output).to_have_text(".")
                body.evaluate("() => { window.holdNext = true; window.pending.shift()(true); }")
                wait_for(lambda: body.evaluate("() => window.pending.length") == 1)
                expect(output).to_have_text(".")
                panel.get_by_role("button", name="Close dashboard", exact=True).click()
                expect(panel).to_have_count(0)
                attempts = body.evaluate("() => { window.pending.shift()(true); return window.attempts; }")
                wait_for_neighbor_updates()
                self.assertEqual(body.evaluate("() => window.attempts"), attempts)

                body.evaluate("() => { window.holdNext = true; }")
                board.get_by_text("Panels", exact=True).click()
                board.get_by_role("button", name="Main · Workflow · Overview", exact=True).click()
                wait_for(lambda: body.evaluate("() => window.pending.length") == 1)
                expect(output).to_have_text("No update")
                wait_for_neighbor_updates()
                self.assertEqual(body.evaluate("() => window.attempts"), attempts + 1)
                body.evaluate("() => window.pending.shift()(false)")
                expect(output).to_have_text(".")
