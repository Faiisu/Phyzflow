import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urljoin
from urllib.request import urlopen

from playwright.sync_api import expect
from workspace_sdk.ui_settings import load_ui_settings

from browser_support import browser_page, local_ui


class WorkspaceSheetTests(unittest.TestCase):
    def test_cli_selects_and_creates_workspace_sheets_from_another_directory(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "Selected Workspace"
            cli = Path(sys.executable).with_name("workspace")
            selected = [str(cli), "--project", str(root)]

            def command(*arguments, check=True):
                return subprocess.run([*selected, *arguments], cwd=parent, capture_output=True, text=True, check=check)

            self.assertIn("does not exist", command("sheets", check=False).stderr)
            command("init")
            self.assertEqual(json.loads((root / "setting.json").read_text())["port"], 18080)
            metadata = (root / "pyproject.toml").read_bytes()
            self.assertIn(b"workspace-sdk==", metadata)
            self.assertNotEqual(command("init", check=False).returncode, 0)
            self.assertEqual((root / "pyproject.toml").read_bytes(), metadata)
            source = Path(command("new-sheet", "First Flow").stdout.strip())
            starter = source.read_text()
            self.assertNotEqual(command("new-sheet", "first flow", check=False).returncode, 0)
            self.assertNotEqual(command("new-sheet", "../outside", check=False).returncode, 0)
            self.assertEqual(source.read_text(), starter)
            (root / "flow.py").write_text(starter)
            source.write_text(starter.replace(
                'return {"message": "Hello from Workspace"}',
                'from pathlib import Path\n    return {"message": Path("value.txt").read_text()}',
            ))
            (source.parent / "value.txt").write_text("from sheet")
            command("check", "--sheet", "First Flow")
            self.assertEqual(json.loads(command("run", "--sheet", "First Flow", "{}").stdout), {"message": "from sheet"})
            self.assertIn("No sheet named", command("check", "--sheet", "Unknown", check=False).stderr)
            self.assertEqual([item["name"] for item in json.loads(command("sheets").stdout)], ["Main", "First Flow"])
            (root / "setting.json").write_text('{"port": 0, "zoom": {"speed": 2}}')
            with local_ui(parent, selected, port=None) as (url, _):
                page.goto(url)
                expect(page.get_by_role("tab", name="Main")).to_be_visible()
                page.get_by_role("tab", name="First Flow").click()
                frame = page.frame_locator('iframe[title="First Flow flow"]')
                expect(frame.get_by_role("button", name="Inspect Start")).to_be_visible()
                self.assertEqual(page.request.get(f"{url}/flows/1/view/project").json()["zoom"]["speed"], 2)
                command("new-sheet", "Added Live")
                expect(page.get_by_role("tab", name="Added Live")).to_be_visible()

    def test_creating_a_sheet_writes_and_opens_a_runnable_python_flow(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            name = "ทดสอบ Flow"
            source = root / "flows" / name / "flow.py"
            with local_ui(root) as (url, _):
                page.goto(url)
                expect(page.get_by_role("status")).to_have_text("Create a sheet to get started.")
                page.get_by_role("button", name="New sheet").click()
                dialog = page.get_by_role("dialog", name="New sheet")
                title = dialog.get_by_label("Sheet name")
                expect(title).to_be_focused()
                title.fill("../outside")
                title.press("Enter")
                expect(dialog.get_by_role("alert")).to_contain_text("Start with a letter or number")
                self.assertFalse((root / "outside").exists())

                title.fill(name)
                title.press("Enter")
                expect(dialog).to_be_hidden()
                expect(page.get_by_role("tab", name=name)).to_have_attribute("aria-selected", "true")
                self.assertTrue(source.is_file())
                original = source.read_bytes()
                flow = page.frame_locator(f'iframe[title="{name} flow"]')
                expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()
                flow.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("tab", name=name)).to_contain_text("Succeeded")
                self.assertEqual(json.loads(flow.locator("#result pre").inner_text()), {"message": "Hello from Workspace"})

                page.get_by_role("button", name="New sheet").click()
                title.fill(name.upper())
                title.press("Enter")
                expect(dialog.get_by_role("alert")).to_contain_text("already in use")
                self.assertEqual(source.read_bytes(), original)
                dialog.get_by_role("button", name="Cancel").click()

            result = subprocess.check_output(
                [str(Path(sys.executable).with_name("workspace")), "run", "{}"], cwd=source.parent, text=True,
            )
            self.assertEqual(json.loads(result), {"message": "Hello from Workspace"})
            (root / "flow.py").write_bytes(original)
            with local_ui(root) as (url, _):
                page.goto(url)
                expect(page.get_by_role("tab", name="Main")).to_have_attribute("aria-selected", "true")
                expect(page.get_by_role("button", name="Delete Main", exact=True)).to_have_count(0)
                self.assertEqual(page.request.post(f"{url}/flows/0/delete").status, 400)
                self.assertEqual((root / "flow.py").read_bytes(), original)
                page.get_by_role("tab", name=name).click()
                expect(page.frame_locator(f'iframe[title="{name} flow"]').get_by_role("button", name="Inspect Start")).to_be_visible()

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

                reload_flow = page.get_by_role("button", name="Reload flow")
                reload_flow.click()
                expect(page.locator("#reload-message")).to_contain_text("This flow is running")
                expect(second_tab).to_contain_text("Running")
                first_tab.click()
                first_source = root / "flows" / "First" / "flow.py"
                original = first_source.read_text()
                first_source.write_text("def invalid(\n")
                reload_flow.click()
                expect(page.locator("#notice-message")).to_contain_text("flow.py:1")
                updated = first_source.parent / "updated.py"
                updated.write_text((first_source.parent / "steps.py").read_text().replace("First ", "Updated "))
                first_source.write_text(original.replace("Finish", "Updated").replace("from steps", "from updated"))
                reload_flow.click()
                expect(first.get_by_role("button", name="Inspect Updated")).to_be_visible()
                self.assertEqual(page.request.post(f"{first_url}run", data={"text": "flow"}).status, 409)
                updated_path = page.locator('iframe[title="First flow"]').get_attribute("src")
                assert updated_path is not None
                self.assertEqual(page.request.get(f"{url}{updated_path}project").json()["steps"][0]["file"], str(updated))
                first.get_by_role("button", name="Run flow").click()
                expect(first_tab).to_contain_text("Succeeded")
                self.assertEqual(json.loads(first.locator("#result pre").inner_text()), {"result": "Updated flow"})
                expect(second_tab).to_contain_text("Running")
                (root / "flows" / "Second" / "release").touch()
                expect(second_tab).to_contain_text("Succeeded")
                second_tab.click()
                (root / "flows" / "Second" / "release").unlink()
                second.get_by_role("button", name="Run flow").click()
                expect(second_tab).to_contain_text("Running")

                first_tab.click()
                (root / "flows" / "First" / "release").unlink()
                first.get_by_role("button", name="Run flow").click()
                expect(first_tab).to_contain_text("Running")
                remove = page.get_by_role("button", name="Delete First", exact=True)
                remove.click()
                dialog = page.get_by_role("dialog", name="Delete sheet?")
                expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
                dialog.get_by_role("button", name="Cancel").click()
                self.assertTrue((root / "flows" / "First" / "flow.py").is_file())
                remove.click()
                dialog.get_by_role("button", name="Delete sheet", exact=True).click()
                expect(first_tab).to_have_count(0)
                expect(second_tab).to_have_attribute("aria-selected", "true")
                expect(second_tab).to_contain_text("Running")
                self.assertFalse((root / "flows" / "First").exists())
                archived, = (root / ".workspace" / "deleted-flows").glob("*/First")
                self.assertTrue((archived / "flow.py").is_file())
                self.assertEqual(len(list((archived / ".workspace" / "runs").glob("*.json"))), 2)
                self.assertEqual(page.request.get(first_url).status, 404)
                page.reload()
                expect(first_tab).to_have_count(0)
                expect(second_tab).to_contain_text("Running")

            self.assertEqual(process.returncode, 0)
            for child_url in (first_url, second_url):
                with self.assertRaises(OSError):
                    urlopen(child_url, timeout=1)
