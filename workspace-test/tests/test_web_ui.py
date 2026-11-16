import json
import os
from pathlib import Path
import py_compile
from shutil import copy2, copytree
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui


PROJECT = Path(__file__).resolve().parents[1]


class WebUITests(unittest.TestCase):
    def test_history_explains_an_incorrect_successful_result(self):
        fixture = PROJECT / "evidence-demo"
        sample = json.loads((fixture / "sample.json").read_text())
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            for filename in ("flow.py", "sample.json"):
                copy2(fixture / filename, root)

            with local_ui(root) as (url, _):
                page.goto(url)
                page.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("status")).to_have_text("Succeeded")
                actual = json.loads(page.locator("#result pre").inner_text())
                self.assertNotEqual(actual, sample["output"])
                self.assertEqual(actual, {"category": "billing", "priority": "standard"})

                history = page.get_by_role("region", name="Run history")
                history.get_by_role("button", name="Refresh history").click()
                history.get_by_text("Succeeded", exact=True).click()
                predict = history.locator(".history-node").filter(
                    has=page.get_by_role("heading", name="Predict · Succeeded")
                )
                expect(predict.get_by_text("The demo predictor assigns every ticket to billing.")).to_be_visible()
                expect(predict.get_by_text("Input sample", exact=True)).to_be_visible()
                expect(predict.get_by_text("Output sample", exact=True)).to_be_visible()
                expect(predict.locator("pre").filter(has_text='"the app crashes with a blank screen"')).to_be_visible()
                expect(predict.locator("pre").filter(has_text='"billing"')).to_be_visible()
                for name in ("Prepare", "Decide"):
                    other = history.locator(".history-node").filter(
                        has=page.get_by_role("heading", name=f"{name} · Succeeded")
                    )
                    expect(other.get_by_text("Input", exact=True)).to_be_visible()
                    expect(other.get_by_text("Output", exact=True)).to_be_visible()
                    expect(other.get_by_text("sample", exact=False)).to_have_count(0)
                    expect(other).not_to_contain_text("assigns every ticket")

    def test_history_recovers_disconnected_run_and_keeps_loaded_source_version(self):
        secret = "request-value-8d708"
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            source = root / "node.py"
            source.write_text(
                "import time\n"
                "from pathlib import Path\n"
                "import torch\n"
                "from helper import fail\n"
                "def prepare(text):\n"
                "    return torch.nn.Parameter(torch.zeros((2, 3)))\n"
                "def predict(text, prepared):\n"
                "    while not Path('finish').exists():\n"
                "        time.sleep(0.01)\n"
                "    try:\n"
                "        fail(text)\n"
                "    except RuntimeError as error:\n"
                "        raise type(text, (RuntimeError,), {})(text) from error\n"
            )
            (root / "helper.py").write_text(
                "def fail(text):\n"
                "    exec(compile('raise RuntimeError(text)', text, 'exec'), {'text': text})\n"
            )
            (root / "flow.py").write_text(
                "from node import prepare, predict\n"
                "from workspace_sdk import Flow, Input, Output, Step\n"
                "flow = Flow(('text',), (\n"
                "    Step('Prepare', prepare, {'text': Input('text')}),\n"
                "    Step('Predict', predict, {'text': Input('text'), 'prepared': Output('Prepare')}),\n"
                "), Output('Predict'))\n"
            )

            with local_ui(root) as (url, _):
                # The running UI has already imported the original node.
                source.write_text("\n" + source.read_text().split("    try:\n")[0] + "    return text\n")
                page.goto(url)
                button = page.get_by_role("button", name="Run flow")
                expect(button).to_be_enabled()
                page.locator("#input").fill(json.dumps({"text": secret}))
                button.click()
                predict = page.locator(".node").filter(has=page.get_by_role("heading", name="Predict", exact=True))
                expect(predict.get_by_text("Running", exact=True)).to_be_visible()
                browser = page.context.browser
                assert browser is not None
                page.close()
                (root / "finish").touch()
                reopened = browser.new_page()
                reopened.goto(url)
                history = reopened.get_by_role("region", name="Run history")
                expect(history).to_be_visible()
                deadline = monotonic() + 5
                while not reopened.request.get(f"{url}/history").json() and monotonic() < deadline:
                    sleep(0.05)
                history.get_by_role("button", name="Refresh history").click()
                expect(history.get_by_text("Failed", exact=True)).to_be_visible(timeout=5000)
                history.get_by_text("Failed", exact=True).click()
                expect(history.get_by_role("heading", name="Predict · Failed")).to_be_visible()
                expect(history.get_by_text("RuntimeError", exact=True)).to_be_visible()
                expect(history.get_by_text("node.py:13", exact=True)).to_be_visible()
                expect(history.get_by_text("helper.py:2", exact=True)).to_be_visible()
                prepare = history.locator(".history-node").filter(has_text="Prepare · Succeeded")
                self.assertEqual(json.loads(prepare.locator("pre").last.inner_text()), {
                    "type": "tensor", "shape": [2, 3], "dtype": "torch.float32", "device": "cpu",
                })
                expect(history).not_to_contain_text(secret)
                expect(history.get_by_text("Duration", exact=False)).to_be_visible()
                version = history.locator(".source-version").inner_text()

            with local_ui(root) as (url, _):
                reopened.goto(url)
                button = reopened.get_by_role("button", name="Run flow")
                expect(button).to_be_enabled()
                reopened.locator("#input").fill(json.dumps({"text": secret}))
                button.click()
                expect(reopened.locator("#result pre")).to_contain_text(secret)
                history = reopened.get_by_role("region", name="Run history")
                history.get_by_role("button", name="Refresh history").click()
                expect(history.get_by_text("Failed", exact=True)).to_be_visible()
                history.get_by_text("Succeeded", exact=True).click()
                expect(history.locator(".source-version")).not_to_have_text(version)
                history.get_by_text("Failed", exact=True).click()
                expect(history.locator(".source-version")).to_have_text(version)
                expect(history.get_by_text("node.py:13", exact=True)).to_be_visible()
            records = list((root / ".workspace" / "runs").glob("*.json"))
            self.assertEqual(len(records), 2)
            for record in records:
                self.assertNotIn(secret, record.read_text())

    def test_run_diagnose_and_restart_with_edited_source(self):
        sample = json.loads((PROJECT / "sample.json").read_text())
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            for filename in ("flow.py", "sample.json", "pyproject.toml"):
                copy2(PROJECT / filename, root)
            copytree(PROJECT / "ticket_demo", root / "ticket_demo")
            source = root / "ticket_demo" / "prediction.py"
            source.write_text(
                "import time\n\n" + source.read_text().replace(
                    "    if text", "    time.sleep(0.75)\n    if text"
                )
            )
            py_compile.compile(str(source), doraise=True)

            with local_ui(root) as (url, _):
                page.goto(url)
                button = page.get_by_role("button", name="Run flow")
                expect(button).to_be_enabled()
                expect(page.get_by_text("Depends on Prepare, Predict", exact=True)).to_have_count(1)
                expect(page.get_by_text("Depends on Prepare", exact=True)).to_have_count(1)
                expect(page.get_by_text("ticket_demo/entrypoint.py:4", exact=True)).to_be_visible()
                expect(page.locator("#input")).to_have_value(json.dumps(sample["input"], indent=2))

                button.click()
                predict = page.locator(".node").filter(has=page.get_by_role("heading", name="Predict", exact=True))
                expect(predict.get_by_text("Running", exact=True)).to_be_visible()
                expect(page.get_by_role("status")).to_have_text("Succeeded")
                self.assertEqual(json.loads(page.locator("#result pre").inner_text()), sample["output"])
                expect(button).to_be_enabled()

                page.locator("#input").fill(json.dumps(sample["failure_input"]))
                button.click()
                expect(page.get_by_role("status")).to_have_text("Failed")
                expect(page.get_by_role("heading", name="Predict failed")).to_be_visible()
                expect(page.get_by_text("RuntimeError", exact=True)).to_be_visible()
                expect(page.locator("#result .error p")).to_have_text("ticket_demo/prediction.py:6")
                expect(page.locator("#result pre")).to_contain_text("The demo predictor failed.")
                decide = page.locator(".node").filter(has=page.get_by_role("heading", name="Decide", exact=True))
                expect(decide.get_by_text("Skipped", exact=True)).to_be_visible()
                screenshot = os.environ.get("WORKSPACE_TEST_SCREENSHOT")
                if screenshot:
                    page.screenshot(path=screenshot)
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.screenshot(path=screenshot.replace(".png", "-mobile.png"), full_page=True)
                    self.assertTrue(page.evaluate("document.documentElement.scrollWidth === innerWidth"))

            original = source.stat()
            source.write_text(source.read_text().replace('"billing"', '"support"'))
            os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
            with local_ui(root) as (url, _):
                page.goto(url)
                page.get_by_role("button", name="Run flow").click()
                expect(page.locator("#result pre")).to_contain_text('"support"')


if __name__ == "__main__":
    unittest.main()
