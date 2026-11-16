import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urljoin
from urllib.request import urlopen

from playwright.sync_api import expect
from workspace_sdk.ui_settings import load_ui_settings

from browser_support import browser_page, local_ui


class WorkspaceSheetTests(unittest.TestCase):
    def test_ui_port_uses_settings_and_accepts_an_explicit_override(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(load_ui_settings(root, None).port, 18080)
            self.assertEqual(load_ui_settings(root, None).zoom, {"speed": 1.0, "smooth_ms": 160})
            settings = root / "setting.json"
            settings.write_text('{"port": 22080, "zoom":{"speed":2,"smooth_ms":0}}')
            self.assertEqual(load_ui_settings(root, None).port, 22080)
            self.assertEqual(load_ui_settings(root, 0).port, 0)
            self.assertEqual(load_ui_settings(root, 0).zoom, {"speed": 2, "smooth_ms": 0})
            settings.write_text('{"port": true}')
            with self.assertRaisesRegex(ValueError, "UI port"):
                load_ui_settings(root, None)
            for zoom, field in (({"speed": 0}, "speed"), ({"smooth_ms": True}, "smooth_ms")):
                settings.write_text(json.dumps({"zoom": zoom}))
                with self.assertRaisesRegex(ValueError, rf"zoom\.{field}"):
                    load_ui_settings(root, None)

    def test_flows_run_independently_and_stop_only_the_requested_sheet(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            zoom = {"speed": 1.5, "smooth_ms": 240}
            (root / "setting.json").write_text(json.dumps({"port": 0, "zoom": zoom}))
            for name in ("First", "Second"):
                directory = root / "flows" / name
                directory.mkdir(parents=True)
                (directory / "flow.py").write_text(
                    "from steps import finish\n"
                    "from workspace_sdk import Flow, Input, Output, Step\n"
                    "flow = Flow(('text',), (Step('Finish', finish, {'text': Input('text')}),), Output('Finish'))\n"
                )
                (directory / "steps.py").write_text(
                    "from pathlib import Path\n"
                    "from time import sleep\n"
                    "def finish(text):\n"
                    "    while not Path('release').exists():\n"
                    "        sleep(0.02)\n"
                    f"    return {{'result': '{name} ' + text}}\n"
                )
                (directory / "sample.json").write_text('{"input":{"text":"flow"}}')
            invalid = root / "flows" / "Third"
            invalid.mkdir()
            (invalid / "flow.py").write_text("def incomplete(\n")

            with local_ui(root, port=None) as (url, process):
                self.assertNotEqual(url, "http://127.0.0.1:18080")
                requests = []
                page.on("request", lambda request: requests.append(request.url))
                page.goto(url)
                first_tab = page.get_by_role("tab", name="First", exact=True)
                second_tab = page.get_by_role("tab", name="Second", exact=True)
                first = page.frame_locator('iframe[title="First flow"]')
                second = page.frame_locator('iframe[title="Second flow"]')
                first.get_by_role("button", name="Run flow").click()
                expect(first_tab).to_contain_text("Running")
                first_path = page.locator('iframe[title="First flow"]').get_attribute("src")
                self.assertEqual(first_path, "/flows/0/view/")
                first_url = urljoin(url, first_path)
                self.assertEqual(page.request.get(f"{first_url}project").json()["zoom"], zoom)

                first_tab.press("ArrowRight")
                expect(second_tab).to_have_attribute("aria-selected", "true")
                second.get_by_role("button", name="Run flow").click()
                expect(second_tab).to_contain_text("Running")
                expect(second.locator(".node .state")).to_have_text("Running")
                second_path = page.locator('iframe[title="Second flow"]').get_attribute("src")
                self.assertEqual(second_path, "/flows/1/view/")
                second_url = urljoin(url, second_path)
                self.assertEqual(page.request.get(f"{second_url}project").json()["zoom"], zoom)
                expect(first_tab).to_contain_text("Running")

                page.get_by_role("button", name="Stop First").click()
                expect(first_tab).to_contain_text("Stopped")
                expect(second_tab).to_have_attribute("aria-selected", "true")
                expect(second_tab).to_contain_text("Running")
                self.assertEqual(page.request.get(first_url).status, 503)

                (root / "flows" / "Second" / "release").touch()
                expect(second_tab).to_contain_text("Succeeded")
                self.assertEqual(json.loads(second.locator("#result pre").inner_text()), {"result": "Second flow"})
                self.assertEqual(len(page.request.get(f"{second_url}history").json()), 1)

                first_tab.click()
                page.get_by_role("button", name="Open flow").click()
                (root / "flows" / "First" / "release").touch()
                first.get_by_role("button", name="Run flow").click()
                expect(first_tab).to_contain_text("Succeeded")
                self.assertEqual(json.loads(first.locator("#result pre").inner_text()), {"result": "First flow"})
                self.assertEqual(len(list((root / "flows" / "First" / ".workspace" / "runs").glob("*.json"))), 1)
                self.assertEqual(len(list((root / "flows" / "Second" / ".workspace" / "runs").glob("*.json"))), 1)
                self.assertEqual(page.locator('iframe[title="First flow"]').get_attribute("src"), first_path)

                third_tab = page.get_by_role("tab", name="Third", exact=True)
                third_tab.click()
                expect(third_tab).to_contain_text("Unavailable")
                expect(page.locator("#notice-message")).to_contain_text("Syntax")
                expect(second_tab).to_contain_text("Succeeded")
                self.assertTrue(requests)
                self.assertTrue(all(request.startswith(f"{url}/") for request in requests), requests)
                second_tab.click()
                (root / "flows" / "Second" / "release").unlink()
                second.get_by_role("button", name="Run flow").click()
                expect(second_tab).to_contain_text("Running")

            self.assertEqual(process.returncode, 0)
            for child_url in (first_url, second_url):
                with self.assertRaises(OSError):
                    urlopen(child_url, timeout=1)
