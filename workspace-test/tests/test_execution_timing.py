from datetime import datetime
import json
from pathlib import Path
import re
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui, show_panel


TELEMETRY = Path(__file__).resolve().parents[1] / "sheets-demo" / "flows" / "Telemetry Control Room"


class ExecutionTimingTests(unittest.TestCase):
    def test_inspector_tracks_live_and_saved_telemetry_timing(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp) / "Telemetry Control Room"
            copytree(TELEMETRY, root, ignore=ignore_patterns(".workspace", "__pycache__"))
            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                outline = page.get_by_role("complementary", name="Flow outline")
                inspector = page.get_by_role("region", name="Node inspector")
                label = inspector.locator("#execution-run-label")
                state = inspector.locator("#execution-status")
                started = inspector.locator("#execution-started")
                finished = inspector.locator("#execution-finished")
                duration = inspector.locator("#execution-duration")
                run_flow = page.get_by_role("button", name="Run flow", exact=True)
                expect(run_flow).to_be_enabled()

                def select_node(name):
                    outline.get_by_role("button", name=re.compile(name)).click()

                def timestamp(field):
                    value = field.locator("time").get_attribute("datetime")
                    assert value is not None
                    return datetime.fromisoformat(value)

                select_node("Run Parallel Pipeline")
                inspector.get_by_role("tab", name="Connections").press("ArrowRight")
                expect(inspector.get_by_role("tab", name="Execution")).to_be_focused()
                show_panel(page, "Run")
                page.locator("#input").fill(json.dumps({"config": {"events": 1000, "score_rounds": 500}}))
                run_flow.click()

                expect(state).to_have_text("Running")
                expect(label).to_have_text(re.compile(r"^Current run · [0-9a-f]{32}$"))
                expect(started.locator("time")).to_be_visible()
                expect(finished).to_have_text("Running")
                expect(page.get_by_role("status")).to_have_text("Succeeded", timeout=15000)
                expect(state).to_have_text("Succeeded")
                expect(duration).to_have_text(re.compile(r" ms$"))
                self.assertLess(timestamp(started), timestamp(finished))
                run_id = label.inner_text().split(" · ")[-1]
                page.get_by_role("button", name="Close Run", exact=True).click()

                def graph_column(name):
                    node = page.locator(".node").filter(has=page.get_by_role("heading", name=name, exact=True))
                    return node.evaluate("node => getComputedStyle(node).gridColumnStart")

                self.assertEqual(graph_column("Reconcile Events"), graph_column("Aggregate Alerts"))
                select_node("Reconcile Events")
                reconcile_finished = timestamp(finished)
                select_node("Aggregate Alerts")
                aggregate_started = timestamp(started)
                self.assertLessEqual(reconcile_finished, aggregate_started)

                record = json.loads((root / ".workspace" / "runs" / f"{run_id}.json").read_text())
                legacy_id = "1" * 32
                legacy_steps = [step.copy() for step in record["steps"] if step["id"] != "Aggregate Alerts"]
                for step in legacy_steps:
                    del step["started_at"], step["finished_at"], step["duration_ms"]
                (root / ".workspace" / "runs" / f"{legacy_id}.json").write_text(json.dumps({
                    **record, "id": legacy_id, "steps": legacy_steps,
                }))

                show_panel(page, "History")
                history = page.get_by_role("region", name="Run history")
                history.get_by_role("button", name="Refresh history").click()

                def select_saved(run):
                    history.locator(f'.history-run[data-run-id="{run}"]').click()
                    expect(label).to_have_text(f"Saved run · {run}")

                select_saved(legacy_id)
                expect(inspector.get_by_text("This node has no execution record in the selected run.")).to_be_visible()
                select_node("Reconcile Events")
                expect(started).to_have_text("Unavailable")
                expect(duration).to_have_text("Unavailable")
                self.assertEqual(timestamp(finished), reconcile_finished)

                select_saved(run_id)
                select_node("Aggregate Alerts")
                self.assertEqual(timestamp(started), aggregate_started)
                page.get_by_role("button", name="Close History", exact=True).click()
                show_panel(page, "Run")
                page.locator("#input").fill(json.dumps({"config": {"simulate": "score"}}))
                run_flow.click()
                expect(page.get_by_role("status")).to_have_text("Failed")
                expect(label).to_have_text(f"Saved run · {run_id}")
                expect(state).to_have_text("Succeeded")
                self.assertEqual(timestamp(started), aggregate_started)

                inspector.get_by_role("button", name="Show current run").click()
                expect(label).to_have_text(re.compile(r"^Current run · [0-9a-f]{32}$"))
                self.assertNotIn(run_id, label.inner_text())
                expect(state).to_have_text("Skipped")
                expect(started).to_have_text("Not run")
                expect(finished).to_have_text("Not run")
                expect(duration).to_have_text("Not measured")
