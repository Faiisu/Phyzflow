import json
from pathlib import Path
import re
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui, show_panel


TELEMETRY = Path(__file__).resolve().parents[1] / "sheets-demo" / "flows" / "Telemetry Control Room"


class ExecutionMetricsTests(unittest.TestCase):
    def test_inspector_shows_queue_and_worker_measurements_for_the_selected_run(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            project = Path(temp) / TELEMETRY.name
            copytree(TELEMETRY, project, ignore=ignore_patterns(".workspace", "__pycache__"))
            with local_ui(project, single_flow=True) as (url, _):
                page.goto(url)
                outline = page.get_by_role("complementary", name="Flow outline")
                outline.get_by_role("button", name=re.compile("Run Parallel Pipeline")).click()
                inspector = page.get_by_role("region", name="Node inspector")
                inspector.get_by_role("tab", name="Execution").click()
                snapshot = inspector.get_by_role("region", name="Operational metrics")
                expect(snapshot).to_be_hidden()

                def run(capacity):
                    show_panel(page, "Run")
                    page.locator("#input").fill(json.dumps({"config": {"events": 80, "queue_capacity": capacity}}))
                    page.get_by_role("button", name="Run flow", exact=True).click()
                    expect(page.get_by_role("status")).to_have_text("Succeeded")
                    result = page.locator("#result pre")
                    expect(result).to_be_visible()
                    report = json.loads(result.inner_text())["pipeline"]
                    page.get_by_role("button", name="Close Run", exact=True).click()
                    return report

                def check_snapshot(report):
                    expect(snapshot).to_contain_text("Completed run snapshot")
                    expect(snapshot.get_by_role("heading", level=4)).to_have_text([
                        "Ingress queue", "I/O workers", "Scoring queue", "CPU workers",
                    ])

                    def read(stage, label):
                        group = snapshot.get_by_role("group", name=stage, exact=True)
                        return float(group.locator("dt").get_by_text(label, exact=True).evaluate(
                            "term => term.nextElementSibling.textContent"
                        ))

                    waits = {}
                    expected_waits = {}
                    for stage, queue, wait in (
                        ("Ingress queue", "ingress", "p95_ingress_wait_ms"),
                        ("Scoring queue", "ready_to_score", "p95_ready_wait_ms"),
                    ):
                        metrics = report["queues"][queue]
                        self.assertEqual(read(stage, "Capacity"), metrics["capacity"])
                        self.assertEqual(read(stage, "Peak depth"), metrics["peak_depth"])
                        self.assertEqual(read(stage, "Blocked enqueues"), metrics["blocked_puts"])
                        waits[stage] = read(stage, "p95 wait (ms)")
                        expected_waits[stage] = report[wait]
                    self.assertEqual(waits, expected_waits)
                    self.assertEqual(read("I/O workers", "Peak overlap"), report["peak_io_overlap"])
                    self.assertEqual(read("CPU workers", "Peak overlap"), report["peak_cpu_overlap"])

                first = run(1)
                check_snapshot(first)
                run_id = inspector.locator("#execution-run-label").inner_text().split(" · ")[-1]
                second = run(6)
                check_snapshot(second)

                show_panel(page, "History")
                history = page.get_by_role("region", name="Run history")
                history.get_by_role("button", name="Refresh history").click()
                history.locator(f'.history-run[data-run-id="{run_id}"]').click()
                check_snapshot(first)
                record = page.request.get(f"{url}/history/{run_id}").json()
                step = next(node for node in record["steps"] if node["id"] == "Run Parallel Pipeline")
                self.assertIn("metrics", step)
                self.assertNotIn("input_sample", step)
                self.assertNotIn("output_sample", step)
                outline.get_by_role("button", name=re.compile("Reconcile Events")).click()
                expect(snapshot).to_be_hidden()
