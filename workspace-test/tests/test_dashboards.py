import json
from pathlib import Path
import re
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui


TELEMETRY = Path(__file__).resolve().parents[1] / "sheets-demo" / "flows" / "Telemetry Control Room"
PIPELINE = "Run Parallel Pipeline"


class DashboardTests(unittest.TestCase):
    def test_workflow_and_node_dashboards_follow_runs_and_apply_settings(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            project = root / "flows" / TELEMETRY.name
            copytree(TELEMETRY, project, ignore=ignore_patterns(".workspace", "__pycache__"))
            with local_ui(root) as (url, _):
                page.goto(url)
                sheet = page.frame_locator(f'iframe[title="{TELEMETRY.name} flow"]')
                sheet.locator("#visualize-workflow").click()
                dashboard = sheet.frame_locator("#dashboard-preview iframe")
                expect(dashboard.get_by_role("heading", name=TELEMETRY.name)).to_be_visible()

                def start(events, failure="none"):
                    if not sheet.locator("#input").is_visible():
                        sheet.locator("#panel-switcher summary").click()
                        sheet.locator('#panel-switcher button[aria-controls="panel-run"]').click()
                        sheet.locator("#panel-switcher summary").click()
                    sheet.locator("#input").fill(json.dumps({
                        "config": {"events": events, "score_rounds": 320, "simulate": failure},
                    }))
                    sheet.locator("#run-flow").click()

                start(55, "score")
                expect(dashboard.locator("#run-state")).to_contain_text("failed")
                expect(dashboard.get_by_text(re.compile(r"ms · failed"))).to_be_visible()
                expect(dashboard.get_by_text("skipped", exact=True).first).to_be_visible()
                expect(sheet.locator("#status")).to_have_text("Failed")
                runs = page.request.get(f"{url}/flows/0/view/history").json()
                failed_id = runs[0]["id"]
                expect(sheet.locator(f'#dashboard-run option[value="{failed_id}"]')).to_have_count(1)
                sheet.locator("#dashboard-run").select_option(failed_id)
                dashboard.get_by_role("button", name=PIPELINE).click()
                expect(dashboard.locator("#pipeline-state")).to_contain_text("failed")
                expect(sheet.locator("#dashboard-run")).to_have_value(failed_id)
                snapshot = dashboard.locator("body").evaluate("""() => {
                    let snapshot;
                    workspaceDashboard.subscribe(data => { snapshot = data; })();
                    return snapshot;
                }""")
                self.assertEqual([step["id"] for step in snapshot["graph"]], [PIPELINE])
                self.assertEqual([step["id"] for step in snapshot["run"]["steps"]], [PIPELINE])
                self.assertFalse(snapshot["run"]["steps"][0].keys() & {
                    "input", "output", "input_sample", "output_sample", "traceback",
                })

                field = dashboard.get_by_label("Submissions per worker")
                feedback = dashboard.locator("#setting-result")
                expect(field).to_have_value("2")
                graph = page.request.get(f"{url}/flows/0/view/project").json()["steps"]
                index = next(i for i, step in enumerate(graph) if step["id"] == PIPELINE)
                settings_url = f"{url}/flows/0/view/dashboard-settings/{index}"
                base = {"nodeId": PIPELINE, "name": "Submissions per worker", "mode": "next"}
                for data in ({"value": 9}, {"value": True}, {"value": 3, "nodeId": "Create Batch"},
                             {"value": 3, "name": "Unknown"}, {"value": 3, "mode": "live", "runId": failed_id}):
                    self.assertEqual(page.request.post(settings_url, data={**base, **data}).status, 400)

                field.fill("3")
                dashboard.get_by_role("button", name="Save for next run").click()
                expect(feedback).to_have_text("Saved 3 for the next run.")
                sheet.get_by_role("button", name="Reload dashboard").click()
                expect(field).to_have_value("3")

                sheet.get_by_role("button", name="Close dashboard").click()
                sheet.locator("#outline-list").get_by_role("button", name=re.compile(PIPELINE)).click()
                sheet.locator("#visualize-node").click()
                expect(dashboard.get_by_role("heading", name="Parallel pipeline")).to_be_visible()

                def metric(label):
                    return dashboard.locator("dt").get_by_text(label, exact=True).locator("xpath=following-sibling::dd[1]")

                start(1800)
                expect(dashboard.locator("#pipeline-state")).to_have_text("running")
                expect(metric("Pipeline progress · Submission limit")).to_have_text("9")
                expect(dashboard.locator("#prepared-label")).to_contain_text("/ 1800 prepared")
                field.fill("1")
                dashboard.get_by_role("button", name="Apply to live run").click()
                expect(feedback).to_contain_text("Applied 1 at")
                expect(metric("Pipeline progress · Submission limit")).to_have_text("3")
                expect(sheet.locator("#status")).to_have_text("Succeeded", timeout=15000)
                expect(metric("CPU workers · Submission limit")).to_have_text("3")
                self.assertEqual(page.request.get(settings_url).json()[PIPELINE]["Submissions per worker"]["value"], 3)

                sheet.locator("#dashboard-run").select_option(failed_id)
                expect(dashboard.locator("#pipeline-state")).to_contain_text("failed")
                expect(dashboard.locator("#progress")).to_be_hidden()
                latest = page.request.get(f"{url}/flows/0/view/history").json()[0]["id"]
                record = page.request.get(f"{url}/flows/0/view/history/{latest}").json()
                step = next(item for item in record["steps"] if item["id"] == PIPELINE)
                self.assertFalse(step.keys() & {"output_preview", "live_metrics", "setting_change"})
                self.assertNotIn("Pipeline progress", step["metrics"])

            with local_ui(root) as (url, _):
                page.goto(url)
                sheet = page.frame_locator(f'iframe[title="{TELEMETRY.name} flow"]')
                sheet.locator("#outline-list").get_by_role("button", name=re.compile(PIPELINE)).click()
                sheet.locator("#visualize-node").click()
                expect(sheet.frame_locator("#dashboard-preview iframe").get_by_label("Submissions per worker")).to_have_value("3")

    def test_dashboard_files_reload_and_cannot_read_workspace_or_navigate_to_network(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            project = Path(temp).resolve()
            (project / "flow.py").write_text(
                'from workspace_sdk import Flow, Output, Step\n'
                'flow = Flow((), (Step("Start", lambda: 1, {}),), Output("Start"))\n'
            )
            node = project / "dashboards" / "nodes" / "Start"
            node.mkdir(parents=True)
            (node / "index.html").write_text('<h1>Original</h1>')
            (node / "style.css").write_text("h1 { color: rgb(7, 8, 9); }")
            script = node / "dashboard.js"
            script.write_text("""
                let isolated = false;
                try { parent.document.body; } catch { isolated = true; }
                document.body.dataset.isolated = isolated;
                fetch("https://outside.test/fetch").catch(() => {
                  document.body.dataset.blocked = true;
                });
                window.addEventListener("message", event => {
                  if (event.data === "navigate") location.href = "https://outside.test/navigate";
                });
            """)
            outside = []
            page.on("request", lambda request: outside.append(request.url) if "outside.test" in request.url else None)
            page.route("https://outside.test/**", lambda route: route.fulfill(body="Network reached"))
            with local_ui(project, single_flow=True) as (url, _):
                page.goto(url)
                page.get_by_role("button", name="Inspect Start").click()
                page.locator("#visualize-node").click()
                frame = page.frame_locator("#dashboard-preview iframe")
                expect(frame.locator("h1")).to_have_text("Original")
                expect(frame.locator("h1")).to_have_css("color", "rgb(7, 8, 9)")
                expect(frame.locator("body")).to_have_attribute("data-isolated", "true")
                expect(frame.locator("body")).to_have_attribute("data-blocked", "true")
                with page.expect_event("framenavigated", predicate=lambda frame: frame.url.startswith("chrome-error:")):
                    page.locator("#dashboard-preview iframe").evaluate('frame => frame.contentWindow.postMessage("navigate", "*")')
                self.assertEqual(outside, [])

                (node / "index.html").write_text("<h1>Updated</h1>")
                page.get_by_role("button", name="Reload dashboard").click()
                expect(frame.locator("h1")).to_have_text("Updated")

                script.unlink()
                script.symlink_to(project / "flow.py")
                page.get_by_role("button", name="Reload dashboard").click()
                expect(page.locator("#dashboard-message")).to_contain_text("inside the dashboard folder")
                self.assertEqual(page.request.get(f"{url}/dashboard-assets/999").status, 404)
                self.assertEqual(page.request.get(f"{url}/dashboard-assets/%2F..%2Fflow.py").status, 404)
