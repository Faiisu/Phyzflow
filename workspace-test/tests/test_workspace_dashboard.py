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

                workspace.click()
                workspace_url = page.url.rstrip("/")
                sources_url = f"{workspace_url}/workspace-dashboard/default/data"
                dialog = page.get_by_role("dialog", name="Workspace dashboard")
                dashboard = page.get_by_role("button", name="Dashboard", exact=True)
                dashboard.click()
                frame = dialog.frame_locator('iframe[title="Overview"]')
                expect(frame.get_by_text("No saved run")).to_have_count(2)
                expect(frame.get_by_text("Waiting for a live run")).to_have_count(2)
                expect(frame.owner).to_have_attribute("sandbox", "allow-scripts allow-forms")
                self.assertEqual(frame.locator("html").evaluate("() => typeof workspaceDashboard.setSetting"), "undefined")
                expect(page.locator('iframe[title="Hand Detection flow"]')).to_have_count(0)

                size = dialog.bounding_box()
                viewport = page.viewport_size
                handle = dialog.get_by_role("button", name="Resize dashboard").bounding_box()
                x = handle["x"] + handle["width"] / 2
                y = handle["y"] + handle["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x - 390, y - 125, steps=10)
                page.mouse.up()
                self.assertEqual(page.viewport_size, viewport)
                self.assertLess(dialog.bounding_box()["width"], 420)
                self.assertLess(dialog.bounding_box()["height"], size["height"])
                self.assertTrue(frame.locator("html").evaluate("root => root.scrollWidth === root.clientWidth"))

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
                live = sources()
                self.assertNotEqual(live["live-count"]["run_id"], live["live-scored"]["run_id"])
                self.assertTrue(live["live-count"]["timestamp"])
                self.assertTrue(live["live-scored"]["timestamp"])

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
