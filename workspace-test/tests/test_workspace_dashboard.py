from base64 import b64decode
import json
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application
from test_application_lifecycle import create_workspace, wait_for
from workspace_sdk.sheets import create_sheet


EXAMPLE = Path(__file__).resolve().parents[1] / "sheets-demo/dashboards/workspace"
FLOW = '''from pathlib import Path
from time import sleep

from workspace_sdk import Flow, Output, Step, publish_metrics, record_metrics


def measure():
    root = Path(__file__).parent
    value = int((root / "value").read_text())
    for _ in range(400):
        publish_metrics("Sample", {"Value": value, "Undeclared": 999})
        if (root / "finish").exists():
            record_metrics("Sample", {"Value": value})
            return "private-output"
        sleep(.05)
    raise TimeoutError("The test did not release the flow.")


flow = Flow((), (Step("Measure", measure, {}),), Output("Measure"))
'''


CAMERA_FLOW = '''from pathlib import Path
from time import sleep

from workspace_sdk import Flow, Output, Step, publish_frame, publish_output

root = Path(__file__).parent


def other_camera():
    publish_frame((root / "other.jpg").read_bytes())


def camera():
    frame = (root / "camera.jpg").read_bytes()
    for _ in range(500):
        publish_output({"preview": "private-preview"})
        publish_frame(frame)
        if (root / "finish").exists():
            return
        sleep(.05)
    raise TimeoutError("The test did not release the camera.")


flow = Flow((), (Step("Other Camera", other_camera, {}), Step("Live Camera", camera, {})), Output("Live Camera"))
'''


class WorkspaceDashboardTests(unittest.TestCase):
    def test_workspace_dashboard_combines_saved_runs_and_unopened_live_sheets(self):
        with TemporaryDirectory() as temp, browser_page(show_scrollbars=True) as page:
            parent = Path(temp).resolve()
            state = parent / "state"
            root = parent / "workspace"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                hand = create_sheet(root, "Hand Detection").parent
                outside = parent / "other-workspace"
                outside.mkdir()
                (outside / "flow.py").write_text(FLOW, encoding="utf-8")
                for sheet, value in ((root, 12), (hand, 34)):
                    (sheet / "flow.py").write_text(FLOW, encoding="utf-8")
                    (sheet / "value").write_text(str(value), encoding="utf-8")

                panel = root / "dashboards/workspace"
                copytree(EXAMPLE, panel)
                manifest = panel / "bridge.json"
                declaration = json.loads(manifest.read_text(encoding="utf-8"))
                for source, sheet in zip(declaration["sources"], (".", "flows/Hand Detection") * 2, strict=True):
                    source["sheet"] = sheet
                    if "metric" in source:
                        source["metric"] = {"step": "Measure", "group": "Sample", "name": "Value"}
                declaration["sources"].extend((
                    {"id": "saved-metric", "sheet": ".", "metric": {"step": "Measure", "group": "Sample", "name": "Value"}},
                    {"id": "outside", "sheet": str(outside), "field": "status"},
                ))
                manifest.write_text(json.dumps(declaration), encoding="utf-8")
                health = panel / "panels" / "run-state"
                health.mkdir(parents=True)
                (health / "index.html").write_text("<h1>Run state</h1><output></output>", encoding="utf-8")
                (health / "bridge.json").write_text(json.dumps({"versions": [1], "sources": [
                    {"id": "status", "sheet": ".", "mode": "live", "field": "status"},
                ]}), encoding="utf-8")
                (health / "dashboard.js").write_text(
                    'workspaceDashboard.subscribe(({sources}) => {\n'
                    '  document.querySelector("output").textContent = sources[0].value ?? "Waiting";\n'
                    '});\n', encoding="utf-8",
                )

                workspace.click()
                workspace_url = page.url.rstrip("/")
                sources_url = f"{workspace_url}/workspace-dashboard/default/data"
                layout_url = f"{workspace_url}/dashboard/visualize-layout"
                dialog = page.get_by_role("dialog", name="Workspace dashboard")
                dashboard = page.get_by_role("button", name="Dashboard", exact=True)
                dashboard.click()
                board = dialog.frame_locator('iframe[title="Workspace Dashboard"]')
                frame = board.frame_locator('iframe[title="Overview"]')
                run_state = board.frame_locator('iframe[title="run state"]')
                expect(frame.get_by_text("No saved run")).to_have_count(2)
                expect(run_state.locator("output")).to_have_text("Waiting")
                expect(board.locator("body")).to_have_class("is-visualize-mode is-workspace-dashboard")
                expect(frame.get_by_text("Waiting for a live run")).to_have_count(2)
                expect(frame.owner).to_have_attribute("sandbox", "allow-scripts allow-forms")
                self.assertEqual(frame.locator("html").evaluate("() => typeof workspaceDashboard.setSetting"), "undefined")
                expect(page.locator('iframe[title="Hand Detection flow"]')).to_have_count(0)

                viewport = page.viewport_size
                self.assertEqual(dialog.bounding_box(), {"x": 0, "y": 0, **viewport})
                overview = board.get_by_role("region", name="Overview", exact=True)
                secondary = board.get_by_role("region", name="run state", exact=True)
                original = overview.bounding_box()
                self.assertGreater(secondary.bounding_box()["x"], original["x"] + original["width"])
                handle = overview.locator('[data-dash="resize"]').bounding_box()
                x = handle["x"] + handle["width"] / 2
                y = handle["y"] + handle["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x - 390, y - 125, steps=10)
                page.mouse.up()
                self.assertEqual(page.viewport_size, viewport)
                resized = overview.bounding_box()
                self.assertLess(resized["width"], 420)
                self.assertLess(resized["height"], original["height"])
                self.assertTrue(frame.locator("html").evaluate("root => root.scrollWidth === root.clientWidth"))
                wait_for(lambda: page.request.get(layout_url).json()["panels"].get('[null,"default"]', {}).get("width")
                         == resized["width"])

                overview.get_by_role("button", name="Full screen", exact=True).click()
                expect(board.locator(".dashboard-panel:fullscreen")).to_have_count(1)
                overview.get_by_role("button", name="Exit full screen", exact=True).click()
                expect(board.locator(".dashboard-panel:fullscreen")).to_have_count(0)

                board.locator("#panel-switcher summary").click()
                board.get_by_role("button", name="Full screen dashboard", exact=True).click()
                expect(board.locator("html:fullscreen")).to_have_count(1)
                board.get_by_role("button", name="Exit full screen dashboard", exact=True).click()
                expect(board.locator("html:fullscreen")).to_have_count(0)
                toggle = board.locator('[data-vis-panel="run-state"]')
                toggle.click()
                expect(secondary).to_be_hidden()
                toggle.click()
                expect(run_state.locator("output")).to_have_text("Waiting")
                board.locator("#panel-switcher summary").click()

                def sources():
                    response = page.request.get(sources_url)
                    self.assertTrue(response.ok, response.text())
                    snapshot = response.json()
                    self.assertNotIn("private-output", response.text())
                    self.assertNotIn("Undeclared", response.text())
                    return {source["id"]: source for source in snapshot["sources"]}

                def command(path):
                    return page.request.post(f"{workspace_url}/{path}", headers={"Origin": url})

                self.assertEqual(sources()["outside"]["state"], "sheet_unavailable")
                with page.context.browser.new_context() as anonymous:
                    self.assertEqual(anonymous.request.get(sources_url).status, 401)
                self.assertEqual(command("run-all").status, 202)
                expect(frame.get_by_text("12", exact=True)).to_be_visible(timeout=20_000)
                expect(frame.get_by_text("34", exact=True)).to_be_visible(timeout=20_000)
                expect(run_state.locator("output")).to_have_text("running")
                live = sources()
                self.assertNotEqual(live["live-count"]["run_id"], live["live-scored"]["run_id"])
                self.assertTrue(live["live-count"]["timestamp"])
                self.assertTrue(live["live-scored"]["timestamp"])
                overview.get_by_role("button", name="Reload dashboard", exact=True).click()
                expect(frame.get_by_text("34", exact=True)).to_be_visible()

                for sheet in (root, hand):
                    (sheet / "finish").touch()
                wait_for(lambda: all(sources()[key]["state"] == "available"
                                     for key in ("counter-duration", "telemetry-duration")))
                expect(frame.locator(".chart-value").filter(has_text="ms")).to_have_count(2)
                saved = sources()
                for saved_id, live_id in (("counter-duration", "live-count"), ("telemetry-duration", "live-scored")):
                    self.assertGreater(saved[saved_id]["value"], 0)
                    self.assertEqual(saved[saved_id]["run_id"], live[live_id]["run_id"])
                self.assertEqual(saved["saved-metric"]["value"], 12)

                page.reload()
                dashboard.click()
                expect(frame.locator(".chart-value").filter(has_text="ms")).to_have_count(2)
                expect(overview).to_have_css("width", f"{int(resized['width'])}px")
                self.assertEqual(sources()["telemetry-duration"], saved["telemetry-duration"])

                for sheet in (root, hand):
                    (sheet / "finish").unlink()
                self.assertEqual(command("run-all").status, 202)
                expect(frame.get_by_text("34", exact=True)).to_be_visible(timeout=20_000)
                self.assertEqual(command("flows/1/stop").status, 200)
                expect(frame.get_by_text("Waiting for a live run")).to_have_count(1)
                expect(frame.get_by_text("12", exact=True)).to_be_visible()
                self.assertEqual(command("flows/1/delete").status, 200)
                expect(frame.get_by_text("Sheet unavailable")).to_have_count(2)
                expect(frame.get_by_text("12", exact=True)).to_be_visible()
                (root / "finish").touch()
                page.set_viewport_size({"width": 390, "height": 844})
                expect(board.locator(".graph-scroll")).to_have_attribute("data-layout-fitted", "")
                first = overview.bounding_box()
                second = secondary.bounding_box()
                self.assertEqual(first["x"], second["x"])
                self.assertGreater(second["y"], first["y"] + first["height"])
                self.assertLessEqual(second["x"] + second["width"], 390)
                board.get_by_role("button", name="Back to sheets").click()
                expect(dialog).to_be_hidden()
                expect(dashboard).to_be_focused()


    def test_workspace_panels_receive_only_declared_live_frames(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            state, root = parent / "state", parent / "workspace"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                second = create_sheet(root, "Second Camera").parent
                images = page.evaluate('''() => ["#ff0000", "#00ff00", "#0000ff"].map(color => {
                    const image = document.createElement("canvas");
                    image.width = image.height = 8;
                    const context = image.getContext("2d");
                    context.fillStyle = color;
                    context.fillRect(0, 0, 8, 8);
                    return image.toDataURL("image/jpeg");
                })''')
                for sheet, image in zip((root, second), images[:2], strict=True):
                    (sheet / "flow.py").write_text(CAMERA_FLOW, encoding="utf-8")
                    (sheet / "camera.jpg").write_bytes(b64decode(image.split(",")[1]))
                    (sheet / "other.jpg").write_bytes(b64decode(images[2].split(",")[1]))

                panels = root / "dashboards/workspace"
                for name, sheet in (("camera-1", "."), ("camera-2", "flows/Second Camera")):
                    panel = panels / "panels" / name
                    panel.mkdir(parents=True)
                    (panel / "index.html").write_text('<img alt="Live camera" hidden>', encoding="utf-8")
                    (panel / "dashboard.js").write_text(
                        """workspaceDashboard.subscribe(({sources: [source]}) => {
  const image = document.querySelector("img");
  image.hidden = !source.value;
  if (source.value) image.src = source.value;
  else image.removeAttribute("src");
});""", encoding="utf-8",
                    )
                    (panel / "bridge.json").write_text(json.dumps({"versions": [1], "sources": [
                        {"id": "jpeg", "sheet": sheet, "mode": "live", "frame": {"step": "Live Camera"}},
                    ]}), encoding="utf-8")
                (panels / "index.html").write_text("Overview", encoding="utf-8")
                (panels / "bridge.json").write_text(json.dumps({"versions": [1], "sources": [
                    {"id": "status", "sheet": ".", "mode": "live", "field": "status"},
                ]}), encoding="utf-8")

                workspace.click()
                workspace_url = page.url.rstrip("/")

                def response(panel):
                    return page.request.get(f"{workspace_url}/workspace-dashboard/{panel}/data")

                def source(panel):
                    return response(panel).json()["sources"][0]

                manifest = panels / "panels/camera-1/bridge.json"
                valid = manifest.read_text(encoding="utf-8")
                manifest.write_text(valid.replace('"live"', '"latest"'), encoding="utf-8")
                self.assertEqual(response("camera-1").status, 400)
                manifest.write_text(valid, encoding="utf-8")
                self.assertEqual(source("camera-1")["state"], "no_data")

                page.get_by_role("button", name="Dashboard", exact=True).click()
                board = page.get_by_role("dialog", name="Workspace dashboard").frame_locator(
                    'iframe[title="Workspace Dashboard"]'
                )
                first = board.frame_locator('iframe[title="camera 1"]').locator("img")
                second_image = board.frame_locator('iframe[title="camera 2"]').locator("img")
                expect(first).to_be_hidden()
                self.assertEqual(page.request.post(f"{workspace_url}/run-all", headers={"Origin": url}).status, 202)
                for index, (name, image) in enumerate((("camera-1", first), ("camera-2", second_image))):
                    expect(image).to_have_attribute("src", images[index], timeout=20_000)
                    self.assertTrue(image.evaluate("image => image.naturalWidth === 8"))
                    snapshot = response(name).text()
                    self.assertNotIn(images[2], snapshot)
                    self.assertNotIn("private-preview", snapshot)
                    self.assertEqual(source(name)["state"], "available")
                    self.assertTrue(source(name)["timestamp"])
                self.assertNotEqual(source("camera-1")["run_id"], source("camera-2")["run_id"])
                self.assertNotIn("data:image", response("default").text())
                self.assertEqual(page.locator('iframe[title="Second Camera flow"]').count(), 0)
                with page.context.browser.new_context() as anonymous:
                    endpoint = f"{workspace_url}/workspace-dashboard/camera-1/data"
                    self.assertEqual(anonymous.request.get(endpoint).status, 401)

                self.assertEqual(page.request.post(
                    f"{workspace_url}/flows/1/stop", headers={"Origin": url}
                ).status, 200)
                expect(second_image).not_to_have_attribute("src", images[1])
                expect(first).to_have_attribute("src", images[0])
                (root / "finish").touch()
                expect(first).not_to_have_attribute("src", images[0])
                self.assertEqual(source("camera-1")["state"], "no_data")
                for run in (root / ".workspace/runs").rglob("*.json"):
                    self.assertNotIn(images[0].split(",")[1], run.read_text(encoding="utf-8"))
