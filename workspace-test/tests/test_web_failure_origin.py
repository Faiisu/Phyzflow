import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui, show_panel


class FailureOriginTests(unittest.TestCase):
    def test_run_error_opens_nested_source_and_explains_skips(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            root = Path(temporary).resolve()
            node = root / "nodes" / "predict"
            nested_source = node / "detail" / "score.py"
            nested_source.parent.mkdir(parents=True)
            nested_source.write_text("\n" * 80 + 'def fail():\n    raise ValueError("Nested scoring failed.")\n')
            (root / "outside.py").write_text('def fail():\n    raise ValueError("Outside the node tree.")\n')
            (node / "entrypoint.py").write_text(
                "from .detail.score import fail\n"
                "from outside import fail as fail_outside\n\n"
                "def predict(mode):\n"
                "    if mode == 'outside':\n"
                "        fail_outside()\n"
                "    if mode == 'nested':\n"
                "        fail()\n"
                "    return 'ok'\n"
            )
            (root / "flow.py").write_text(
                "from workspace_sdk import Flow, Input, Output, Step\n"
                "from nodes.predict.entrypoint import predict\n\n"
                "def forward(value):\n"
                "    return value\n\n"
                "flow = Flow(\n"
                "    inputs=('mode',),\n"
                "    steps=(\n"
                "        Step('Predict', predict, {'mode': Input('mode')}),\n"
                "        Step('Decide', forward, {'value': Output('Predict')}),\n"
                "        Step('Report', forward, {'value': Output('Decide')}),\n"
                "        Step('Healthy', forward, {'value': Input('mode')}),\n"
                "    ),\n"
                "    output=Output('Report'),\n"
                ")\n"
            )
            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                run = page.get_by_role("button", name="Run flow", exact=True)
                expect(run).to_be_enabled()
                toolbar = page.locator("#panel-toolbar")
                outline = page.locator("#panel-outline")
                toolbar_box = toolbar.bounding_box()
                self.assertGreaterEqual(outline.bounding_box()["y"], toolbar_box["y"] + toolbar_box["height"])
                page.get_by_role("button", name="Close Toolbar").click()
                expect(run).to_be_hidden()
                self.assertAlmostEqual(outline.bounding_box()["y"], 13.6, delta=1)
                show_panel(page, "Toolbar")
                expect(run).to_be_visible()
                show_panel(page, "Run")
                panel = page.get_by_role("region", name="Run", exact=True)
                flow_status = page.locator("#status")
                input_box = panel.locator("#input")

                input_box.fill(json.dumps({"mode": "nested"}))
                run.click()
                expect(flow_status).to_have_text("Failed")
                error = panel.locator(".error")
                expect(error.get_by_text("nodes/predict/detail/score.py:82", exact=True)).to_be_visible()

                predict = page.get_by_role("button", name="Inspect Predict", exact=True)
                page.get_by_role("button", name="Inspect Healthy").focus()
                page.get_by_role("button", name="Inspect Healthy").press("Enter")
                expect(predict).to_have_attribute("aria-pressed", "false")

                focus = error.get_by_role("button", name="Focus node")
                focus.press("Enter")
                expect(predict).to_be_focused()
                expect(predict).to_have_attribute("aria-pressed", "true")
                expect(predict).to_be_in_viewport()

                error.get_by_role("button", name="Open source").click()
                dialog = page.get_by_role("dialog", name="Predict files")
                expect(dialog.locator("#node-source-name")).to_have_text("nodes/predict/detail/score.py")
                expect(dialog.get_by_text("Line 82 selected.", exact=True)).to_be_visible()
                source_input = dialog.get_by_role("textbox", name="Source code")
                self.assertEqual(source_input.evaluate(
                    "editor => editor.value.slice(editor.selectionStart, editor.selectionEnd).trim()"
                ), 'raise ValueError("Nested scoring failed.")')
                self.assertGreater(source_input.evaluate("editor => editor.scrollTop"), 0)
                dialog.get_by_role("button", name="Close node files").click()

                inspector = page.get_by_role("region", name="Node inspector")
                skipped_reason = inspector.locator("#inspector-blocked-by")
                for name, reason in (
                    ("Decide", "Blocked by Predict (Failed)"),
                    ("Report", "Blocked by Decide (Skipped)"),
                ):
                    selection = page.get_by_role("button", name=f"Inspect {name}", exact=True)
                    selection.focus()
                    selection.press("Enter")
                    expect(skipped_reason).to_have_text(reason)

                healthy = page.locator(".node").filter(has=page.get_by_role("heading", name="Healthy", exact=True))
                expect(healthy).to_have_attribute("data-state", "succeeded")

                input_box.fill(json.dumps({"mode": "outside"}))
                run.click()
                expect(flow_status).to_have_text("Failed")
                expect(error.get_by_text("outside.py:2", exact=True)).to_be_visible()
                expect(error.get_by_role("button", name="Open source")).to_have_count(0)
                expect(error.get_by_role("button", name="Focus node")).to_be_visible()

                input_box.fill(json.dumps({"mode": "success"}))
                run.click()
                expect(flow_status).to_have_text("Succeeded")
                expect(skipped_reason).to_be_hidden()
