from datetime import datetime
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application


class ApplicationFlowStepTests(unittest.TestCase):
    def test_inspect_connected_steps_live_metrics_and_nested_failures(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "Diagnostics"
            with application(parent / "state") as url:
                page.goto(url)
                page.get_by_role("button", name="Create Workspace").click()
                page.get_by_label("New folder path").fill(str(root))
                page.get_by_role("button", name="Create", exact=True).click()
                workspace = page.get_by_role("link", name="Diagnostics")
                expect(workspace).to_be_visible(timeout=20_000)
                (root / "nodes/start/entrypoint.py").write_text(
                    '''from pathlib import Path
from time import monotonic, sleep

from workspace_sdk import publish_metrics, record_metrics

from .detail import require_valid


def wait_for(name):
    deadline = monotonic() + 20
    while not Path(name).exists() and monotonic() < deadline:
        sleep(.02)


def prepare():
    publish_metrics("Indexing", {"Progress (%)": 25, "Rows per second": 17})
    wait_for("advance")
    publish_metrics("Indexing", {"Progress (%)": 60, "Rows per second": 42})
    wait_for("finish")
    record_metrics("Summary", {"Rows processed": 78})
    return 3


def validate(count, fail):
    require_valid(fail)
    return count * 2


def report(count):
    return count
''',
                    encoding="utf-8",
                )
                (root / "nodes/start/detail.py").write_text(
                    'def require_valid(fail):\n    if fail:\n        raise ValueError("Invalid row count.")\n',
                    encoding="utf-8",
                )
                (root / "flow.py").write_text(
                    "from workspace_sdk import Flow, Input, Output, Step\n"
                    "from nodes.start.entrypoint import prepare, report, validate\n"
                    'flow = Flow(("fail",), (\n'
                    '    Step("Prepare", prepare, {}),\n'
                    '    Step("Validate", validate, {"count": Output("Prepare"), "fail": Input("fail")}),\n'
                    '    Step("Report", report, {"count": Output("Validate")}),\n'
                    '), Output("Report"))\n',
                    encoding="utf-8",
                )
                (root / "sample.json").write_text('{"input": {"fail": false}}', encoding="utf-8")
                workspace.click()
                flow = page.frame_locator('iframe[title="Main flow"]')
                inspector = flow.get_by_role("region", name="Node inspector")
                prepare = flow.get_by_role("button", name="Inspect Prepare", exact=True)
                validate = flow.get_by_role("button", name="Inspect Validate", exact=True)
                expect(prepare).to_be_visible()
                inspector.get_by_role("button", name="Validate", exact=True).click()
                expect(validate).to_have_attribute("aria-pressed", "true")
                inspector.get_by_role("button", name="Prepare", exact=True).click()
                expect(prepare).to_have_attribute("aria-pressed", "true")
                inspector.get_by_role("tab", name="Execution").click()
                flow.get_by_role("button", name="Run flow", exact=True).click()
                execution = inspector.get_by_role("tabpanel", name="Execution")
                expect(execution.locator("#execution-status")).to_have_text("Running")
                expect(execution.locator("#execution-finished")).to_have_text("Running")
                started = execution.locator("#execution-started time")
                first_start = started.get_attribute("datetime")
                live = execution.get_by_role("region", name="Live progress and metrics")
                indexing = live.get_by_role("group", name="Indexing")
                expect(indexing.locator("dt")).to_have_text(["Progress (%)", "Rows per second"])
                expect(indexing.locator("dd")).to_have_text(["25", "17"])
                (root / "advance").touch()
                expect(indexing.locator("dd")).to_have_text(["60", "42"])
                expect(execution.locator("#execution-status")).to_have_text("Running")
                expect(started).to_have_attribute("datetime", first_start)
                (root / "finish").touch()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                expect(execution.locator("#execution-status")).to_have_text("Succeeded")
                expect(execution.locator("#execution-duration")).to_have_text(re.compile(r" ms$"))
                prepare_finished = execution.locator("#execution-finished time").get_attribute("datetime")
                expect(live).to_be_hidden()
                snapshot = execution.get_by_role("region", name="Operational metrics")
                expect(snapshot.get_by_role("group", name="Summary").locator("dd")).to_have_text("78")
                validate.press("Enter")
                expect(execution.locator("#execution-status")).to_have_text("Succeeded")
                expect(execution.locator("#execution-duration")).to_have_text(re.compile(r" ms$"))
                validate_started = started.get_attribute("datetime")
                self.assertLessEqual(datetime.fromisoformat(prepare_finished), datetime.fromisoformat(validate_started))

                flow.get_by_role("textbox", name="Run input JSON").fill('{"fail": true}')
                flow.get_by_role("button", name="Run flow", exact=True).click()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Failed")
                expect(execution.locator("#execution-status")).to_have_text("Failed")
                error = flow.get_by_role("region", name="Run", exact=True)
                expect(error.get_by_role("heading", name="Validate failed", exact=True)).to_be_visible()
                expect(error.get_by_text("ValueError", exact=True)).to_be_visible()
                expect(error.get_by_text("nodes/start/detail.py:3", exact=True)).to_be_visible()
                error.get_by_role("button", name="Open source").click()
                dialog = flow.get_by_role("dialog", name="Validate files")
                expect(dialog.get_by_text("Line 3 selected.", exact=True)).to_be_visible()
                dialog.get_by_role("button", name="Close node files").click()
                flow.get_by_role("button", name="Inspect Report", exact=True).press("Enter")
                expect(execution.locator("#execution-status")).to_have_text("Skipped")
                expect(inspector.get_by_text("Blocked by Validate (Failed)", exact=True)).to_be_visible()
