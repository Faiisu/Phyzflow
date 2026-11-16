import json
import os
from pathlib import Path
import py_compile
from shutil import copy2, copytree
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui


PROJECT = Path(__file__).resolve().parents[1]


class WebUITests(unittest.TestCase):
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
