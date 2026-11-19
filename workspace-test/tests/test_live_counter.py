import json
from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui


COUNTER = Path(__file__).resolve().parents[1] / "sheets-demo" / "flows" / "Live Counter"
WAIT_FOR_RELEASE = """\
from pathlib import Path
from time import sleep

def wait_for_release():
    while not Path("release").exists():
        sleep(0.01)
"""


class LiveCounterTests(unittest.TestCase):
    def test_dashboard_reconnects_after_browser_refresh(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp)
            copytree(COUNTER, root / "flows" / COUNTER.name, ignore=ignore_patterns(".workspace", "__pycache__"))
            with local_ui(root) as (url, _):
                page.goto(url)
                view = page.frame_locator('iframe[title="Live Counter flow"]')
                view.locator('[data-vis-node=""][data-vis-panel="default"]').click()
                page.get_by_role("button", name="Run flow", exact=True).click()
                dashboard = view.frame_locator("#dashboard-preview iframe")
                expect(dashboard.locator("#count")).to_have_text("3")
                page.reload()
                view.locator('[data-vis-node=""][data-vis-panel="default"]').click()
                expect(dashboard.locator("#count")).not_to_have_text("—", timeout=5000)
                expect(page.get_by_role("button", name="Run flow", exact=True)).to_be_disabled()
                page.get_by_role("button", name="Stop Live Counter").click()

    def test_reconnected_dashboard_receives_completion(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            (root / "flow.py").write_text(
                "from pathlib import Path\nfrom time import sleep\n"
                "from workspace_sdk import Flow, Output, Step, publish_output\n"
                "def work():\n"
                "    publish_output({'ready': True})\n"
                "    while not Path('release').exists(): sleep(0.01)\n"
                "    return 42\n"
                "flow = Flow((), (Step('Work', work, {}),), Output('Work'))\n"
            )
            assets = root / "dashboards" / "workflow"
            assets.mkdir(parents=True)
            (assets / "index.html").write_text('<output id="state">idle</output>')
            (assets / "dashboard.js").write_text(
                "workspaceDashboard.subscribe(s => { document.querySelector('#state').textContent = s.run?.status ?? 'idle'; });"
            )
            with local_ui(root) as (url, _):
                page.goto(url)
                view = page.frame_locator('iframe[title="Main flow"]')
                page.get_by_role("button", name="Run flow", exact=True).click()
                expect(view.get_by_role("region", name="Live output")).to_be_visible()
                page.reload()
                view.locator('[data-vis-node=""][data-vis-panel="default"]').click()
                dashboard = view.frame_locator("#dashboard-preview iframe")
                expect(dashboard.locator("#state")).to_have_text("running")
                (root / "release").touch()
                expect(dashboard.locator("#state")).to_have_text("succeeded")
                expect(page.get_by_role("button", name="Run flow", exact=True)).to_be_enabled()
                expect(view.locator("#result")).to_contain_text("42")
                expect(view.locator(".history-run")).to_have_count(1)

    def test_dashboard_plots_count_and_multiplied_output_over_time(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp)
            copytree(COUNTER, root / "flows" / COUNTER.name, ignore=ignore_patterns(".workspace", "__pycache__"))
            with local_ui(root) as (url, _):
                page.goto(url)
                view = page.frame_locator('iframe[title="Live Counter flow"]')
                view.locator("#visualize-workflow").click()
                dashboard = view.frame_locator("#dashboard-preview iframe")
                expect(dashboard.locator("#chart-status")).to_have_text("Run the flow to start counting.")
                page.get_by_role("button", name="Run flow", exact=True).click()
                expect(dashboard.locator("#count")).to_have_text("3")
                readings = dashboard.locator("dl").evaluate(
                    "list => Array.from(list.querySelectorAll('output'), element => element.value)"
                )
                count, scaled = map(int, readings[:2])
                self.assertGreaterEqual(count, 3)
                self.assertEqual(scaled, count * 10)
                self.assertGreaterEqual(float(readings[2].removesuffix(" s")), 0.9)
                self.assertTrue(dashboard.locator("canvas").evaluate("""canvas => {
                    const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
                    const hasColor = (red, green, blue) => pixels.some((value, index) =>
                        index % 4 === 0 && value === red && pixels[index + 1] === green &&
                        pixels[index + 2] === blue && pixels[index + 3] === 255
                    );
                    return hasColor(134, 199, 255) && hasColor(255, 191, 120);
                }"""))
                page.get_by_role("button", name="Stop Live Counter").click()
                expect(page.get_by_role("tab", name=COUNTER.name, exact=True)).to_contain_text("Stopped")
                self.assertFalse(list((root / "flows" / COUNTER.name / ".workspace" / "runs").glob("*.json")))
                page.get_by_role("button", name="Open flow", exact=True).click()
                view.locator("#visualize-workflow").click()
                expect(dashboard.locator("#count")).to_have_text("—")

    def test_streams_latest_value_until_stopped_and_can_start_again(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp)
            sheet = root / "flows" / COUNTER.name
            copytree(COUNTER, sheet, ignore=ignore_patterns(".workspace", "__pycache__"))
            (root / "flow.py").write_text(
                "from workspace_sdk import Flow, Output, Step\n"
                "def hello(): return 'hello'\n"
                "flow = Flow((), (Step('Hello', hello, {}),), Output('Hello'))\n"
            )
            flow_file = sheet / "flow.py"
            flow_file.write_text(flow_file.read_text().replace(
                "flow = Flow(", "def prepare(): return None\n\nflow = Flow(",
            ).replace("steps=(\n", 'steps=(\n        Step("Prepare", prepare, {}),\n'))
            with local_ui(root) as (url, _):
                page.goto(url)
                tab = page.get_by_role("tab", name=COUNTER.name, exact=True)
                tab.click()
                view = page.frame_locator('iframe[title="Live Counter flow"]')
                run = page.get_by_role("button", name="Run flow", exact=True)
                run.click()
                output = view.get_by_role("region", name="Live output").locator("pre")
                expect(output).to_be_visible()
                first = json.loads(output.inner_text())
                expect(output).not_to_have_text(output.inner_text())
                later = json.loads(output.inner_text())
                self.assertGreater(later["count"], first["count"])
                self.assertEqual(later["output"], later["count"] * 10)
                expect(output).to_have_count(1)
                expect(view.locator(".node .state")).to_have_text(["Succeeded", "Running", "Running"])
                expect(view.locator("#status")).to_have_text("Running")
                self.assertEqual(page.request.get(f"{url}/flows/1/view/history").json(), [])
                page.get_by_role("button", name="Stop Live Counter").click()
                expect(view.locator("#status")).to_have_text("Stopped")
                expect(view.locator(".node .state")).to_have_text(["Succeeded", "Stopped", "Stopped"])
                snapshot = output.inner_text()
                expect(run).to_be_disabled()
                self.assertEqual(page.request.get(f"{url}/flows/1/view/").status, 503)
                page.get_by_role("tab", name="Main", exact=True).click()
                tab.click()
                expect(output).to_have_text(snapshot)
                view.get_by_role("button", name="Inspect Count Up").dblclick()
                editor = view.get_by_role("dialog", name="Count Up files")
                editor.get_by_role("button", name="entrypoint.py", exact=True).click()
                expect(editor.locator("#node-source-name")).to_have_text("nodes/count_up/entrypoint.py")
                expect(editor.get_by_role("textbox", name="Source code")).to_be_editable()
                editor.get_by_role("button", name="Close node files").click()
                page.get_by_role("button", name="Open flow", exact=True).click()
                expect(view.locator(".node .state")).to_have_text(["Idle", "Idle", "Idle"])
                run.click()
                expect(output).to_contain_text('"count": 1')
                expect(view.locator(".node .state")).to_have_text(["Succeeded", "Running", "Running"])
                page.get_by_role("button", name="Stop Live Counter").click()
                expect(tab).to_contain_text("Stopped")

    def test_finite_generator_succeeds_after_it_has_finished(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp)
            copytree(COUNTER, root, dirs_exist_ok=True, ignore=ignore_patterns(".workspace", "__pycache__"))
            entrypoint = root / "nodes/count_up/entrypoint.py"
            entrypoint.write_text(WAIT_FOR_RELEASE + (
                "\ndef count_up():\n    yield 23\n    yield 24\n    wait_for_release()\n"
            ))
            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                page.get_by_role("button", name="Run flow", exact=True).click()
                preview = page.get_by_role("region", name="Live output")
                expect(preview).to_contain_text('"output": 240')
                expect(page.locator(".node .state")).to_have_text(["Running", "Running"])
                (root / "release").touch()
                expect(page.get_by_role("heading", name="Output", exact=True)).to_be_visible()
                expect(page.get_by_role("status")).to_have_text("Succeeded")
                expect(page.locator(".node .state")).to_have_text(["Succeeded", "Succeeded"])
                producer = page.locator(".node").filter(has=page.get_by_role("heading", name="Count Up", exact=True))
                expect(producer.locator(".node-duration")).to_contain_text("ms")
                self.assertEqual(json.loads(page.locator("#result > pre").inner_text()), {"count": 24, "output": 240})
                runs = page.request.get(f"{url}/history").json()
                detail = page.request.get(f"{url}/history/{runs[0]['id']}").json()
                self.assertEqual(detail["status"], "succeeded")
                self.assertEqual([node["status"] for node in detail["steps"]], ["succeeded", "succeeded"])
                self.assertGreater(detail["steps"][0]["duration_ms"], 0)
                self.assertNotIn("output_preview", json.dumps(detail))
                self.assertNotIn('"count"', json.dumps(detail))

    def test_generator_failure_points_to_the_producer_after_it_yields(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp)
            copytree(COUNTER, root, dirs_exist_ok=True, ignore=ignore_patterns(".workspace", "__pycache__"))
            package = root / "nodes/count_up"
            (package / "failure.py").write_text("def fail():\n    raise ValueError('private-data-marker')\n")
            (package / "entrypoint.py").write_text("from .failure import fail\n" + WAIT_FOR_RELEASE + (
                "\ndef count_up():\n    yield 5\n    wait_for_release()\n    fail()\n"
            ))
            flow = root / "flow.py"
            flow.write_text(flow.read_text().replace(
                "flow = Flow(", "def summarize(result): return result\n\nflow = Flow(",
            ).replace(
                '    ),\n    output=Output("Multiply by Ten"),',
                '        Step("Summary", summarize, {"result": Output("Multiply by Ten")}),\n'
                '    ),\n    output=Output("Summary"),',
            ))
            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                page.get_by_role("button", name="Run flow", exact=True).click()
                expect(page.get_by_role("region", name="Live output")).to_contain_text('"output": 50')
                (root / "release").touch()
                expect(page.get_by_role("status")).to_have_text("Failed")
                expect(page.locator(".node .state")).to_have_text(["Failed", "Failed", "Skipped"])
                errors = page.locator("#result article.error")
                consumer = errors.filter(has=page.get_by_role("heading", name="Multiply by Ten failed"))
                expect(consumer).to_contain_text("Blocked by Count Up (Failed)")
                producer = errors.filter(has=page.get_by_role("heading", name="Count Up failed"))
                producer.get_by_role("button", name="Open source").click()
                editor = page.get_by_role("dialog", name="Count Up files")
                expect(editor.locator("#node-source-name")).to_have_text("nodes/count_up/failure.py")
                runs = page.request.get(f"{url}/history").json()
                detail = page.request.get(f"{url}/history/{runs[0]['id']}").json()
                steps = {step["id"]: step for step in detail["steps"]}
                self.assertEqual(steps["Count Up"]["error_type"], "ValueError")
                self.assertIn({"file": "nodes/count_up/failure.py", "line": 2}, steps["Count Up"]["error_frames"])
                self.assertEqual(steps["Multiply by Ten"]["blocked_by"], ["Count Up"])
                self.assertEqual(steps["Summary"]["blocked_by"], ["Multiply by Ten"])
                self.assertNotIn("private-data-marker", json.dumps(detail))
