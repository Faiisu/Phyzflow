import json
from pathlib import Path
from shutil import copytree
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import sleep
import unittest
from urllib.parse import urljoin
from urllib.request import urlopen

from playwright.sync_api import expect
from workspace_sdk.ui_settings import load_ui_settings

from browser_support import browser_page, local_ui, show_panel


class WorkspaceSheetTests(unittest.TestCase):
    def test_editing_sheet_flow_source_saves_to_disk_and_applies_on_reload(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            cli = Path(sys.executable).with_name("workspace")
            subprocess.run([cli, "init", "--with-main"], cwd=root, check=True, capture_output=True)
            source = root / "flow.py"
            original = source.read_text()
            (root / "nodes" / "start" / "finish.py").write_text(
                'def finish():\n    return {"message": "Finished"}\n'
            )
            updated = original.replace(
                'from nodes.start.entrypoint import start',
                'from nodes.start.entrypoint import start\nfrom nodes.start.finish import finish',
            ).replace(
                'steps=(Step("Start", start, {}),),',
                'steps=(Step("Start", start, {}), Step("Finish", finish, {})),',
            ).replace('output=Output("Start")', 'output=Output("Finish")')

            with local_ui(root) as (url, _):
                page.goto(url)
                flow = page.frame_locator('iframe[title="Main flow"]')
                expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()
                edit = page.get_by_role("button", name="Edit flow.py")
                edit.click()
                dialog = page.get_by_role("dialog", name="Edit flow.py")
                editor = dialog.get_by_role("textbox", name="Source code")
                expect(editor).to_have_value(original)
                editor.fill(updated)
                dialog.get_by_role("button", name="Save", exact=True).click()
                expect(dialog.get_by_role("status")).to_contain_text("Saved.")
                self.assertEqual(source.read_text(), updated)
                dialog.get_by_role("button", name="Close", exact=True).click()
                page.get_by_role("button", name="Reload flow").click()
                expect(flow.get_by_role("button", name="Inspect Finish")).to_be_visible()
                flow.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                self.assertEqual(
                    json.loads(flow.locator("#result pre").inner_text()),
                    {"message": "Finished"},
                )

                edit.click()
                expect(editor).to_have_value(updated)
                external = "# coding: unknown-encoding\n" + updated
                source.write_text(external)
                draft = updated + "# Browser draft\n"
                editor.fill(draft)
                dialog.get_by_role("button", name="Save", exact=True).click()
                expect(dialog.get_by_role("status")).to_contain_text("changed on disk")
                expect(editor).to_have_value(draft)
                self.assertEqual(source.read_text(), external)

    def test_editing_flow_source_rejects_an_unreadable_encoding_change(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            cli = Path(sys.executable).with_name("workspace")
            subprocess.run([cli, "init", "--with-main"], cwd=root, check=True, capture_output=True)
            source = root / "flow.py"
            original = source.read_bytes()

            with local_ui(root) as (url, _):
                page.goto(url)
                page.get_by_role("button", name="Edit flow.py").click()
                dialog = page.get_by_role("dialog", name="Edit flow.py")
                editor = dialog.get_by_role("textbox", name="Source code")
                expect(editor).to_have_value(original.decode())

                for encoding in ("unknown-encoding", "latin-1", "base64"):
                    with self.subTest(encoding=encoding):
                        draft = f"# coding: {encoding}\n# Café\n" + original.decode()
                        editor.fill(draft)
                        dialog.get_by_role("button", name="Save", exact=True).click()
                        expect(dialog.get_by_role("status")).to_contain_text("Check the Python encoding declaration")
                        expect(editor).to_have_value(draft)
                        self.assertEqual(source.read_bytes(), original)

    def test_fixing_unavailable_sheet_flow_source_restores_the_graph_and_run(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            cli = Path(sys.executable).with_name("workspace")
            subprocess.run([cli, "init", "--with-main"], cwd=root, check=True, capture_output=True)
            source = root / "flow.py"
            corrected = source.read_text().replace('"Start"', '"Recovered"')
            invalid = "def broken(\n"

            with local_ui(root) as (url, _):
                page.goto(url)
                flow = page.frame_locator('iframe[title="Main flow"]')
                expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()
                edit = page.get_by_role("button", name="Edit flow.py")
                reload_flow = page.get_by_role("button", name="Reload flow")
                dialog = page.get_by_role("dialog", name="Edit flow.py")
                editor = dialog.get_by_role("textbox", name="Source code")
                save = dialog.get_by_role("button", name="Save", exact=True)
                close = dialog.get_by_role("button", name="Close", exact=True)
                status = dialog.get_by_role("status")

                edit.click()
                expect(editor).to_be_editable()
                editor.fill(invalid)
                save.click()
                expect(status).to_contain_text("Saved.")
                self.assertEqual(source.read_text(), invalid)
                close.click()
                reload_flow.click()
                tab = page.get_by_role("tab", name="Main")
                expect(tab).to_contain_text("Unavailable")
                expect(page.get_by_text(f"Syntax: {source}:1", exact=False)).to_be_visible()

                edit.click(timeout=3000)
                expect(editor).to_have_value(invalid)
                external = invalid + "# External edit\n"
                source.write_text(external)
                editor.fill(corrected)
                save.click()
                expect(status).to_contain_text("changed on disk")
                expect(editor).to_have_value(corrected)
                self.assertEqual(source.read_text(), external)
                page.once("dialog", lambda prompt: prompt.accept())
                close.click()

                edit.click()
                expect(editor).to_have_value(external)
                editor.fill(corrected)
                save.click()
                expect(status).to_contain_text("Saved.")
                self.assertEqual(source.read_text(), corrected)
                close.click()
                reload_flow.click()
                expect(flow.get_by_role("button", name="Inspect Recovered")).to_be_visible()
                flow.get_by_role("button", name="Run flow").click()
                expect(tab).to_contain_text("Succeeded")
                self.assertEqual(
                    json.loads(flow.locator("#result pre").inner_text()),
                    {"message": "Hello from Workspace"},
                )

    def test_init_with_main_creates_a_runnable_project_and_preserves_existing_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / ".venv").mkdir()
            cli = Path(sys.executable).with_name("workspace")

            def command(*arguments, check=True):
                return subprocess.run([cli, *arguments], cwd=root, capture_output=True, text=True, check=check)

            command("init", "--with-main")
            self.assertEqual(
                json.loads(command("sheets").stdout),
                [{"name": "Main", "file": str(root / "flow.py")}],
            )
            command("check", "--sheet", "Main")
            self.assertEqual(json.loads(command("run", "--sheet", "Main", "{}").stdout),
                             {"message": "Hello from Workspace"})
            settings = root / "nodes" / "start" / "settings.json"
            self.assertEqual(json.loads(settings.read_text()), {})
            (root / "nodes" / "start" / "entrypoint.py").write_text('def start(): return "My Workspace"\n')
            self.assertNotEqual(command("init", "--with-main", check=False).returncode, 0)
            self.assertEqual(json.loads(command("run", "--sheet", "Main", "{}").stdout), "My Workspace")

    def test_node_settings_file_opens_by_default_and_sets_next_run_defaults(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            node = root / "nodes" / "start"
            node.mkdir(parents=True)
            (node / "entrypoint.py").write_text(
                'from workspace_sdk import NodeSetting\n'
                'LIMIT = NodeSetting(2, minimum=1, maximum=8)\n'
                'def start(): return LIMIT.get()\n'
            )
            (root / "flow.py").write_text(
                'from workspace_sdk import Flow, Output, Step\n'
                'from nodes.start.entrypoint import LIMIT, start\n'
                'flow = Flow((), (Step("Start", start, {}, settings={"Limit": LIMIT}),), Output("Start"))\n'
            )
            settings = node / "settings.json"
            with local_ui(root) as (url, _):
                page.goto(url)
                flow = page.frame_locator('iframe[title="Main flow"]')
                flow.get_by_role("button", name="Inspect Start").dblclick()
                files = flow.get_by_role("dialog", name="Start files")
                expect(files.locator("#node-source-name")).to_have_text("nodes/start/settings.json")
                editor = files.get_by_role("textbox", name="Source code")
                expect(editor).to_have_value('{\n  "Limit": 2\n}\n')
                self.assertFalse(settings.exists())
                save = files.get_by_role("button", name="Save", exact=True)
                editor.fill('{"Limit": 99}')
                save.click()
                expect(files.locator("#node-source-message")).to_contain_text("Maximum value is 8")
                self.assertFalse(settings.exists())
                editor.fill('{"Limit": 5}')
                save.click()
                expect(files.locator("#node-source-message")).to_contain_text("Saved.")
                self.assertEqual(json.loads(settings.read_text()), {"Limit": 5})
                files.get_by_role("button", name="Close node files").click()
                page.get_by_role("button", name="Reload flow").click()
                expect(page.locator('iframe[title="Main flow"]')).to_have_attribute("src", "/flows/0/revision/1/view/")
                flow.get_by_role("button", name="Run flow", exact=True).click()
                expect(flow.locator("#result pre")).to_have_text("5")

                cli = Path(sys.executable).with_name("workspace")
                result = subprocess.run([str(cli), "run", "{}"], cwd=root, capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout), 5)
                saved = page.request.post(f"{url}/flows/0/revision/1/view/dashboard-settings/workflow", data={
                    "nodeId": "Start", "name": "Limit", "value": 6, "mode": "next",
                })
                self.assertEqual(saved.json()["status"], "saved")
                result = subprocess.run([str(cli), "run", "{}"], cwd=root, capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout), 6)
                settings.write_text('{"Limit": "invalid"}')
                result = subprocess.run([str(cli), "check"], cwd=root, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"Node settings: {settings}: Limit: Expected a int setting.", result.stderr)

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
            self.assertIn("nodes/<node_name>/entrypoint.py", (root / "AGENTS.md").read_text())
            self.assertNotEqual(command("init", check=False).returncode, 0)
            self.assertEqual((root / "pyproject.toml").read_bytes(), metadata)
            source = Path(command("new-sheet", "First Flow").stdout.strip())
            starter = source.read_text()
            self.assertNotEqual(command("new-sheet", "first flow", check=False).returncode, 0)
            self.assertNotEqual(command("new-sheet", "../outside", check=False).returncode, 0)
            self.assertEqual(source.read_text(), starter)
            (root / "flow.py").write_text(starter)
            copytree(source.parent / "nodes", root / "nodes")
            node = source.parent / "nodes" / "start" / "entrypoint.py"
            node.write_text(node.read_text().replace(
                'return {"message": "Hello from Workspace"}',
                'from pathlib import Path\n    return {"message": Path("value.txt").read_text()}',
            ))
            (source.parent / "value.txt").write_text("from sheet")
            helper = node.with_name("helper.py")
            helper.write_text("def broken(\n")
            failed = command("check", "--sheet", "First Flow", check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn(f"Syntax: {helper}:1", failed.stderr)
            helper.unlink()
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
                node = source.parent / "nodes" / "start" / "entrypoint.py"
                self.assertTrue(node.is_file())
                helper = node.parent / "helpers" / "format.py"
                helper.parent.mkdir()
                helper.write_text("VALUE = 1\n")
                config = node.with_name("config.json")
                config.write_text('{"enabled": true, "label": "<img src=x>"}\n')
                sibling = node.parent.parent / "other"
                sibling.mkdir()
                (sibling / "private.py").write_text("SECRET = 2\n")
                linked = node.parent / "linked.py"
                linked.symlink_to(sibling / "private.py")
                flow = page.frame_locator(f'iframe[title="{name} flow"]')
                start = flow.get_by_role("button", name="Inspect Start")
                start.click()
                expect(flow.locator("#inspector-source")).to_contain_text("nodes/start/entrypoint.py")
                files = flow.get_by_role("dialog", name="Start files")
                expect(files).to_be_hidden()
                start.dblclick()
                expect(files.locator("#node-files-path")).to_have_text("nodes/start")
                expect(files.locator("#node-source-name")).to_have_text("nodes/start/settings.json")
                expect(files.get_by_role("textbox", name="Source code")).to_have_value("{}\n")
                expect(files.get_by_text("entrypoint.py", exact=True)).to_be_visible()
                expect(files.get_by_text("format.py", exact=True)).to_be_hidden()
                files.get_by_text("helpers", exact=True).click()
                expect(files.get_by_text("format.py", exact=True)).to_be_visible()
                expect(files.get_by_text("private.py", exact=True)).to_have_count(0)
                expect(files.get_by_text("linked.py", exact=True)).to_have_count(0)
                expect(files.get_by_text("__pycache__", exact=True)).to_have_count(0)
                editor = files.get_by_role("textbox", name="Source code")
                files.get_by_role("button", name="entrypoint.py", exact=True).click()
                saved = node.read_text()
                expect(editor).to_have_value(saved)
                highlight = files.locator("#node-source-highlight")
                expect(highlight.locator(".token.keyword").first).to_have_text("def")
                expect(highlight.locator(".token.string").filter(has_text="Hello from Workspace")).to_be_visible()
                files.get_by_role("button", name="config.json", exact=True).click()
                expect(editor).to_have_value(config.read_text())
                expect(highlight.locator(".token.property").first).to_have_text('"enabled"')
                expect(highlight.locator(".token.boolean")).to_have_text("true")
                expect(highlight.locator("img")).to_have_count(0)
                self.assertIn("<img src=x>", highlight.text_content() or "")
                new_config = '{"enabled": false, "count": 42}\n'
                editor.fill(new_config)
                expect(highlight.locator(".token.boolean")).to_have_text("false")
                expect(highlight.locator(".token.number")).to_have_text("42")
                files.get_by_role("button", name="Save", exact=True).click()
                expect(files.locator("#node-source-message")).to_contain_text("Saved.")
                self.assertEqual(config.read_text(), new_config)
                files.get_by_role("button", name="format.py", exact=True).click()
                expect(editor).to_have_value("VALUE = 1\n")
                files.get_by_role("button", name="entrypoint.py", exact=True).click()
                expect(editor).to_have_value(saved)
                newer = saved.replace("Hello from Workspace", "Saved in the browser")
                node.write_text(newer)
                editor.fill(saved.replace("Hello", "Stale"))
                files.get_by_role("button", name="Save", exact=True).click()
                expect(files.locator("#node-source-message")).to_contain_text("changed on disk")
                self.assertEqual(node.read_text(), newer)
                page.once("dialog", lambda prompt: prompt.accept())
                files.get_by_role("button", name="entrypoint.py", exact=True).click()
                expect(editor).to_have_value(newer)
                edited = newer.replace("Saved in the browser", "Edited in the browser")
                editor.fill(edited)
                page.once("dialog", lambda prompt: prompt.dismiss())
                page.get_by_role("button", name="Reload flow").click()
                expect(editor).to_have_value(edited)
                files.get_by_role("button", name="Save", exact=True).click()
                expect(files.locator("#node-source-message")).to_contain_text("Saved.")
                self.assertEqual(node.read_text(), edited)
                for rejected in ("../flow.py", "nodes/other/private.py", "nodes/start/linked.py"):
                    response = page.request.post(f"{url}/flows/0/view/node-source/0", data={
                        "path": rejected, "source": "Overwritten", "revision": "0" * 64,
                    })
                    self.assertEqual(response.status, 404)
                self.assertEqual((sibling / "private.py").read_text(), "SECRET = 2\n")
                files.get_by_role("button", name="Close node files").press("Escape")
                expect(files).to_be_hidden()
                show_panel(flow, "Inspector")
                flow.get_by_role("button", name="View files").click()
                expect(files.get_by_text("entrypoint.py", exact=True)).to_be_visible()
                files.get_by_role("button", name="Close node files").click()
                linked.unlink()
                page.get_by_role("button", name="Reload flow").click()
                expect(page.locator(f'iframe[title="{name} flow"]')).to_have_attribute("src", "/flows/0/revision/1/view/")
                flow.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("tab", name=name)).to_contain_text("Succeeded")
                self.assertEqual(json.loads(flow.locator("#result pre").inner_text()), {"message": "Edited in the browser"})
                expect(flow.locator("#result .token.property")).to_have_text('"message"')

                page.get_by_role("button", name="New sheet").click()
                title.fill(name.upper())
                title.press("Enter")
                expect(dialog.get_by_role("alert")).to_contain_text("already in use")
                self.assertEqual(source.read_bytes(), original)
                dialog.get_by_role("button", name="Cancel").click()

            result = subprocess.check_output(
                [str(Path(sys.executable).with_name("workspace")), "run", "{}"], cwd=source.parent, text=True,
            )
            self.assertEqual(json.loads(result), {"message": "Edited in the browser"})
            (root / "flow.py").write_bytes(original)
            copytree(source.parent / "nodes", root / "nodes")
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
            self.assertEqual(load_ui_settings(root, None).ipv4, "127.0.0.1")
            self.assertEqual(load_ui_settings(root, None).zoom, {"speed": 1.0, "smooth_ms": 160})
            settings = root / "setting.json"
            settings.write_text('{"port": 22080, "zoom":{"speed":2,"smooth_ms":0}}')
            self.assertEqual(load_ui_settings(root, None).port, 22080)
            self.assertEqual(load_ui_settings(root, 0).port, 0)
            self.assertEqual(load_ui_settings(root, 0).zoom, {"speed": 2, "smooth_ms": 0})
            settings.write_text('{"ipv4": "0.0.0.0"}')
            self.assertEqual(load_ui_settings(root, None).ipv4, "0.0.0.0")
            self.assertEqual(load_ui_settings(root, None, ipv4_override="127.0.0.1").ipv4, "127.0.0.1")
            settings.write_text('{"ipv4": "example.com"}')
            with self.assertRaisesRegex(ValueError, "IPv4"):
                load_ui_settings(root, None)
            settings.write_text('{"port": true}')
            with self.assertRaisesRegex(ValueError, "UI port"):
                load_ui_settings(root, None)
            for zoom, field in (({"speed": 0}, "speed"), ({"smooth_ms": True}, "smooth_ms")):
                settings.write_text(json.dumps({"zoom": zoom}))
                with self.assertRaisesRegex(ValueError, rf"zoom\.{field}"):
                    load_ui_settings(root, None)

    def test_wildcard_ui_accepts_the_connection_address_and_rejects_other_hosts(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp)
            (root / "setting.json").write_text('{"ipv4": "0.0.0.0", "port": 0}')
            (root / "flow.py").write_text(
                'from workspace_sdk import Flow, Output, Step\n'
                'flow = Flow((), (Step("Start", lambda: True, {}),), Output("Start"))\n'
            )
            for single_flow in (False, True):
                with self.subTest(single_flow=single_flow), local_ui(root, port=None, single_flow=single_flow) as (url, _):
                    self.assertTrue(url.startswith("http://127.0.0.1:"))
                    self.assertTrue(page.request.get(url).ok)
                    self.assertEqual(page.request.get(url, headers={"Host": "example.com"}).status, 403)
                    path = "/run" if single_flow else "/flows/0/open"
                    self.assertEqual(page.request.post(f"{url}{path}", headers={"Origin": "http://example.com"}).status, 403)
                    response = page.request.post(f"{url}{path}", data={}, headers={"Origin": url})
                    self.assertTrue(response.ok)
                    if not single_flow:
                        self.assertTrue(page.request.get(url + response.json()["url"] + "project").ok)

    def test_flows_run_independently_and_stop_only_the_requested_sheet(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            zoom = {"speed": 1.5, "smooth_ms": 240}
            (root / "setting.json").write_text(json.dumps({"port": 0, "zoom": zoom}))
            for name in ("First", "Second"):
                directory = root / "flows" / name
                directory.mkdir(parents=True)
                (directory / "flow.py").write_text(
                    "from steps import finish, prepare\n"
                    "from workspace_sdk import Flow, Input, Output, Step\n"
                    "flow = Flow(('text',), (\n"
                    "    Step('Prepare', prepare, {'text': Input('text')}),\n"
                    "    Step('Finish', finish, {'text': Output('Prepare')}),\n"
                    "), Output('Finish'))\n"
                )
                (directory / "steps.py").write_text(
                    "from pathlib import Path\n"
                    "from time import sleep\n"
                    "def prepare(text):\n"
                    "    return text\n"
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
                first.get_by_role("button", name="Inspect Finish", exact=True).click()
                first.get_by_role("button", name="Zoom in", exact=True).click()
                first_path = page.locator('iframe[title="First flow"]').get_attribute("src")
                self.assertEqual(first_path, "/flows/0/view/")
                first_url = urljoin(url, first_path)
                self.assertEqual(page.request.get(f"{first_url}project").json()["zoom"], zoom)

                first_tab.press("ArrowRight")
                expect(second_tab).to_have_attribute("aria-selected", "true")
                second.get_by_role("button", name="Run flow").click()
                expect(second_tab).to_contain_text("Running")
                expect(second.locator(".node").filter(has=second.get_by_role("heading", name="Finish")).locator(".state")).to_have_text("Running")
                second_path = page.locator('iframe[title="Second flow"]').get_attribute("src")
                self.assertEqual(second_path, "/flows/1/view/")
                second_url = urljoin(url, second_path)
                self.assertEqual(page.request.get(f"{second_url}project").json()["zoom"], zoom)
                expect(first_tab).to_contain_text("Running")

                def delay_stop_response(route):
                    response = route.fetch()
                    sleep(0.3)
                    route.fulfill(response=response)

                page.route("**/flows/0/stop", delay_stop_response)
                page.get_by_role("button", name="Stop First").click()
                expect(first_tab).to_contain_text("Stopped")
                expect(second_tab).to_have_attribute("aria-selected", "true")
                expect(second_tab).to_contain_text("Running")
                self.assertEqual(page.request.get(first_url).status, 503)
                first_tab.click()
                expect(page.locator("#notice-message")).to_have_text("Stopped · Run snapshot")
                expect(first.get_by_role("status")).to_have_text("Stopped")
                expect(first.get_by_role("button", name="Inspect Finish", exact=True)).to_have_attribute("aria-pressed", "true")
                expect(first.locator("#zoom-level")).to_have_text("125%")
                expect(first.locator(".node .state")).to_have_text(["Succeeded", "Running"])
                expect(first.get_by_role("button", name="Run flow")).to_be_disabled()
                expect(first.locator("#result .error")).to_have_count(0)
                second_tab.click()

                # Keep the event stream idle past the upstream connection timeout.
                sleep(2.2)
                (root / "flows" / "Second" / "release").touch()
                expect(second_tab).to_contain_text("Succeeded")
                self.assertEqual(json.loads(second.locator("#result pre").inner_text()), {"result": "Second flow"})
                self.assertEqual(len(page.request.get(f"{second_url}history").json()), 1)

                first_tab.click()
                expect(first.locator(".node .state")).to_have_text(["Succeeded", "Running"])
                page.get_by_role("button", name="Open flow").click()
                expect(first.get_by_role("status")).to_have_text("Ready")
                expect(first.locator(".node .state")).to_have_text(["Idle", "Idle"])
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
