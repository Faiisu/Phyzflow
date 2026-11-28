import json
import os
import subprocess
import tomllib
import unittest
from pathlib import Path
from shutil import copy2, copytree
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from browser_support import browser_page, local_ui
from playwright.sync_api import expect
from test_application import application, project_python, sign_in


def add_workspace(page, root):
    page.get_by_role("button", name="Add Workspace").click()
    page.get_by_role("button", name="Enter a folder path").click()
    page.get_by_label("Existing folder path").fill(str(root))
    page.get_by_role("button", name="Add", exact=True).click()


def write_flow(root, body):
    root.mkdir(parents=True, exist_ok=True)
    source = root / "flow.py"
    source.write_text(
        "from workspace_sdk import Flow, Output, Step\n"
        "import sys\n"
        "def observe():\n"
        f"    {body}\n"
        'flow = Flow((), (Step("Observe", observe, {}),), Output("Observe"))\n',
        encoding="utf-8",
    )
    return source


def run_sheet(page, name):
    sheet = page.get_by_role("tab", name=name)
    sheet.click()
    flow = page.frame_locator(f'iframe[title="{name} flow"]')
    page.get_by_role("button", name="Run flow").click()
    expect(sheet).to_contain_text("Succeeded")
    return json.loads(flow.locator("#result pre").inner_text())


class AddWorkspaceTests(unittest.TestCase):
    def test_workspace_format_controls_whether_application_opens_sheets(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            state = parent / "state"
            with application(state) as url:
                sign_in(page, url, state)
                for name, declaration, expected in (
                    ("Supported", "format_version = 1", None),
                    ("Unsupported", "format_version = 2", "found 2"),
                    ("Malformed", 'format_version = "1"', "found '1'"),
                    ("Unspecified", "", None),
                ):
                    combination = tomllib.loads(declaration).get("format_version")
                    with self.subTest(combination=combination):
                        root = parent / name
                        sheet = root / "flows" / "Child"
                        source = write_flow(sheet, "return 73")
                        imported = sheet / "flow-imported"
                        source.write_text(
                            f"from pathlib import Path\nPath({str(imported)!r}).touch()\n"
                            + source.read_text(encoding="utf-8"),
                            encoding="utf-8",
                        )
                        config = root / "pyproject.toml"
                        config.write_text("[tool.workspace]\nformat_version = 1\n")
                        (sheet / "pyproject.toml").write_text("[tool.workspace]\nformat_version = 2\n")
                        headers = {"Origin": url}
                        body = {"action": "add", "path": str(root)}
                        registered = page.request.post(f"{url}/workspaces", data=body, headers=headers)
                        self.assertEqual(registered.status, 201, registered.text())
                        address = f"{url}/workspaces/{registered.json()['id']}/"
                        config.write_text(f"[tool.workspace]\n{declaration}\n")
                        original = config.read_bytes()
                        if expected is not None:
                            response = page.request.get(address)
                            self.assertEqual(response.status, 400, response.text())
                            error = response.json()["error"]
                            self.assertIn(str(config), error)
                            self.assertIn(expected, error)
                            self.assertIn("supported version is 1", error)
                            self.assertFalse(imported.exists())
                        else:
                            page.goto(address)
                            if not declaration:
                                expect(page.get_by_text("Workspace format is unspecified", exact=False)).to_be_visible()
                            self.assertEqual(run_sheet(page, "Child"), 73)
                            self.assertTrue(imported.exists())
                        self.assertEqual(config.read_bytes(), original)

    def test_mounted_workspace_rejects_new_discovery_and_worker_load_for_unsupported_format(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root, state = parent / "Project", parent / "state"
            source = write_flow(root / "flows" / "Child", "return 73")
            config = root / "pyproject.toml"
            config.write_text("[tool.workspace]\nformat_version = 1\n")
            with application(state) as url:
                sign_in(page, url, state)
                headers = {"Origin": url}
                registered = page.request.post(f"{url}/workspaces", data={"action": "add", "path": str(root)}, headers=headers)
                self.assertEqual(registered.status, 201, registered.text())
                address = f"{url}/workspaces/{registered.json()['id']}/"
                self.assertEqual(page.request.get(f"{address}workspace").json()["flows"][0]["name"], "Child")

                declaration = b"[tool.workspace]\nformat_version = 2\n"
                config.write_bytes(declaration)
                marker = source.with_name("flow-imported")
                source.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + source.read_text())
                write_flow(root / "flows" / "Later", "return 74")
                workspace = page.request.get(f"{address}workspace").json()
                self.assertEqual([entry["name"] for entry in workspace["flows"]], ["Child"])
                self.assertIn("found 2", workspace["format_notice"])
                self.assertIn(str(config), workspace["format_notice"])
                self.assertEqual(
                    page.request.post(f"{address}flows", data={"name": "Created"}, headers=headers).status, 400,
                )
                self.assertFalse((root / "flows" / "Created").exists())
                for command in ("open", "reload"):
                    response = page.request.post(f"{address}flows/0/{command}", headers=headers)
                    self.assertIn("found 2", response.text())
                    self.assertIn("supported version is 1", response.text())
                self.assertFalse(marker.exists())
                self.assertEqual(config.read_bytes(), declaration)

                config.write_text("[tool.workspace]\nformat_version = 1\n")
                page.goto(address)
                self.assertEqual(run_sheet(page, "Child"), 73)
                self.assertTrue(marker.exists())
                expect(page.get_by_role("tab", name="Later")).to_be_visible()

    def test_editing_the_workspace_format_during_an_active_run_keeps_its_result(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root, state = parent / "Project", parent / "state"
            finished = root / "finish"
            write_flow(root, (
                "from time import monotonic, sleep\n"
                "    from pathlib import Path\n"
                "    deadline = monotonic() + 20\n"
                f"    while not Path({str(finished)!r}).exists() and monotonic() < deadline:\n"
                "        sleep(.05)\n"
                "    return 75"
            ))
            config = root / "pyproject.toml"
            config.write_text("[tool.workspace]\nformat_version = 1\n")
            with application(state) as url:
                sign_in(page, url, state)
                add_workspace(page, root)
                page.get_by_role("link", name=root.name, exact=True).click(timeout=20_000)
                page.get_by_role("button", name="Run flow").click()
                tab = page.get_by_role("tab", name="Main")
                expect(tab).to_contain_text("Running")
                try:
                    config.write_text("[tool.workspace]\nformat_version = 2\n")
                    expect(page.get_by_text("found 2", exact=False)).to_be_visible()
                    reload = page.request.post(f"{page.url}flows/0/reload", headers={"Origin": url})
                    self.assertEqual(reload.status, 409)
                finally:
                    finished.touch()
                expect(tab).to_contain_text("Succeeded")
                flow = page.frame_locator('iframe[title="Main flow"]')
                self.assertEqual(json.loads(flow.locator("#result pre").inner_text()), 75)

    def test_standalone_workspace_ui_reports_unspecified_format(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            write_flow(root, "return 74")
            with local_ui(root) as (url, _):
                page.goto(url)
                expect(page.get_by_text("Workspace format is unspecified", exact=False)).to_be_visible()
                self.assertEqual(run_sheet(page, "Main"), 74)
            self.assertFalse((root / "pyproject.toml").exists())

    def test_browse_host_folders_to_create_and_add_workspaces(self):
        with TemporaryDirectory(prefix="Phyzflow projects ", dir=Path.home()) as temp, browser_page() as page:
            parent = Path(temp).resolve()
            state = parent / "State"
            project = parent / "Existing project "
            new_root = parent / "New project"
            write_flow(project, "return 72")
            (parent / "notes.txt").touch()
            page.set_viewport_size({"width": 320, "height": 620})

            with application(state) as url:
                headers = {"Origin": url}
                guest = page.request.post(f"{url}/workspace-folders", data={}, headers=headers)
                self.assertEqual(guest.status, 401)
                sign_in(page, url, state)
                self.assertEqual(page.request.post(f"{url}/workspace-folders", data={}).status, 403)
                blocked = page.request.post(f"{url}/workspace-folders", data={"path": str(state)}, headers=headers)
                self.assertEqual(blocked.status, 400)

                page.get_by_role("button", name="Create Workspace").click()
                dialog = page.get_by_role("dialog", name="Create Workspace")
                dialog.get_by_role("button", name=parent.name, exact=True).click()
                expect(dialog.get_by_role("button", name=project.name.strip(), exact=True)).to_be_visible()
                expect(dialog.get_by_role("button", name=state.name, exact=True)).to_have_count(0)
                expect(dialog.get_by_role("button", name="notes.txt")).to_have_count(0)
                self.assertTrue(dialog.evaluate("element => element.scrollWidth === element.clientWidth"))
                name = dialog.get_by_label("New folder name")
                name.fill("../outside")
                dialog.get_by_role("button", name="Create", exact=True).click()
                expect(dialog.get_by_role("alert")).to_contain_text("Enter a single folder name.")
                name.fill(new_root.name)
                dialog.get_by_role("button", name="Create", exact=True).click()
                expect(page.get_by_role("link", name=new_root.name)).to_be_focused(timeout=20_000)
                self.assertTrue((new_root / "flow.py").is_file())
                settings = tomllib.loads((new_root / "pyproject.toml").read_text())["tool"]["workspace"]
                self.assertEqual(settings["format_version"], 1)

                page.get_by_role("button", name="Add Workspace").click()
                dialog = page.get_by_role("dialog", name="Add Workspace")
                dialog.get_by_role("button", name=parent.name, exact=True).click()
                dialog.get_by_role("button", name=new_root.name, exact=True).click()
                expect(dialog.locator("#workspace-location")).to_have_text(str(new_root))
                dialog.get_by_role("button", name="Up").click()
                dialog.get_by_role("button", name=project.name.strip(), exact=True).click()
                expect(dialog.locator("#workspace-location")).to_have_text(str(project))
                browse = dialog.get_by_role("button", name="Browse folders", exact=True)
                enter_path = dialog.get_by_role("button", name="Enter a folder path", exact=True)
                add = dialog.get_by_role("button", name="Add", exact=True)
                expect(browse).to_have_attribute("aria-pressed", "true")
                enter_path.click()
                expect(enter_path).to_have_attribute("aria-pressed", "true")
                expect(dialog.get_by_label("Existing folder path")).to_be_focused()
                expect(add).to_be_disabled()
                browse.click()
                expect(browse).to_have_attribute("aria-pressed", "true")
                expect(dialog.locator("#workspace-location")).to_have_text(str(project))
                add.click()
                expect(page.get_by_role("link", name=project.name.strip())).to_be_focused(timeout=20_000)
                self.assertTrue((project / ".venv").is_dir())

    def test_narrow_workspace_list_wraps_long_paths_and_reveals_new_entries_after_filtering(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            long_root = parent / ("LongWorkspace" * 12)
            second = parent / "Second"
            new_root = parent / "New project"
            long_root.mkdir()
            second.mkdir()
            page.set_viewport_size({"width": 320, "height": 620})

            with application(parent / "state") as url:
                sign_in(page, url, parent / "state")
                add_workspace(page, long_root)
                expect(page.get_by_role("link", name=long_root.name)).to_be_visible(timeout=20_000)
                add_workspace(page, second)
                expect(page.get_by_role("link", name=second.name)).to_be_visible(timeout=20_000)

                viewport_fits = "root => root.scrollWidth === root.clientWidth"
                self.assertTrue(page.locator("html").evaluate(viewport_fits))
                workspace_filter = page.get_by_role("searchbox", name="Filter Workspaces")
                workspace_filter.fill(long_root.name)
                expect(page.get_by_role("link", name=second.name)).to_have_count(0)
                delete = page.get_by_role("button", name=f"Delete {long_root.name}")
                self.assertGreaterEqual(delete.bounding_box()["height"], 44)
                delete.click()
                dialog = page.get_by_role("dialog", name="Delete Workspace?")
                expect(dialog).to_contain_text(str(long_root))
                self.assertTrue(dialog.evaluate(viewport_fits))
                dialog.get_by_role("button", name="Cancel").click()

                page.get_by_role("button", name="Create Workspace").click()
                page.get_by_role("button", name="Enter a folder path").click()
                page.get_by_label("New folder path").fill(str(new_root))
                page.get_by_role("button", name="Create", exact=True).click()
                expect(page.get_by_role("link", name=new_root.name)).to_be_focused(timeout=20_000)
                expect(workspace_filter).to_have_value("")
                self.assertTrue(page.locator("html").evaluate(viewport_fits))

    def test_add_an_existing_project_and_create_its_python_312_environment(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "Existing project"
            state = parent / "state"
            source = write_flow(root, "return [sys.executable, list(sys.version_info[:2]), 'existing flow']")
            original = source.read_bytes()

            with application(state) as url:
                sign_in(page, url, state)
                add_workspace(page, root)
                workspace = page.get_by_role("link", name=root.name)
                expect(workspace).to_be_visible(timeout=20_000)
                self.assertEqual({file.name for file in root.iterdir()}, {"flow.py", ".venv"})
                workspace.click()
                self.assertEqual(run_sheet(page, "Main"), [
                    str(project_python(root)), [3, 12], "existing flow",
                ])
                self.assertEqual(source.read_bytes(), original)

    def test_run_and_debug_the_support_ticket_sample(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "Support tickets"
            root.mkdir()
            state = parent / "state"
            sample_project = Path(__file__).resolve().parents[1]
            for name in ("flow.py", "sample.json", "pyproject.toml"):
                copy2(sample_project / name, root)
            copytree(sample_project / "ticket_demo", root / "ticket_demo")
            sample = json.loads((root / "sample.json").read_text(encoding="utf-8"))

            with application(state) as url:
                sign_in(page, url, state)
                add_workspace(page, root)
                page.get_by_role("link", name=root.name).click(timeout=20_000)
                self.assertEqual(run_sheet(page, "Main"), sample["output"])

                flow = page.frame_locator('iframe[title="Main flow"]')
                flow.get_by_role("textbox", name="Run input JSON").fill(json.dumps(sample["failure_input"]))
                page.get_by_role("button", name="Run flow", exact=True).click()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Failed")
                error = flow.get_by_role("region", name="Run", exact=True)
                expect(error.get_by_role("heading", name="Predict failed", exact=True)).to_be_visible()
                error.get_by_role("button", name="Open source").click()
                dialog = flow.get_by_role("dialog", name="Predict files")
                expect(dialog.get_by_text("Line 3 selected.", exact=True)).to_be_visible()
                dialog.get_by_role("button", name="Close node files").click()

                flow.get_by_role("button", name="Inspect Decide", exact=True).press("Enter")
                inspector = flow.get_by_role("region", name="Node inspector")
                inspector.get_by_role("tab", name="Execution").click()
                expect(inspector.get_by_role("tabpanel", name="Execution").locator("#execution-status")).to_have_text("Skipped")

    def test_both_sheets_reuse_the_existing_environment_and_its_installed_dependency(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "Shared dependencies"
            state = parent / "state"
            body = "import shared_sample; return [sys.executable, shared_sample.answer()]"
            write_flow(root, body)
            write_flow(root / "flows" / "Second", body)
            environment = root / ".venv"
            interpreter = project_python(root)
            managed_python = ["--managed-python"] if os.name == "nt" else []
            subprocess.run(["uv", "venv", "--no-project", *managed_python, "--python", "3.12", environment], check=True)
            wheel = parent / "shared_sample-1.0-py3-none-any.whl"
            with ZipFile(wheel, "w") as archive:
                archive.writestr("shared_sample.py", "def answer(): return 72\n")
                archive.writestr("shared_sample-1.0.dist-info/METADATA",
                                 "Metadata-Version: 2.1\nName: shared-sample\nVersion: 1.0\n")
                archive.writestr("shared_sample-1.0.dist-info/WHEEL",
                                 "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
                archive.writestr("shared_sample-1.0.dist-info/RECORD", "")
            subprocess.run(["uv", "pip", "install", "--offline", "--python", interpreter, wheel], check=True)
            config = environment / "pyvenv.cfg"
            original_config = config.read_bytes()

            with application(state) as url:
                sign_in(page, url, state)
                add_workspace(page, root)
                page.get_by_role("link", name=root.name).click()
                for name in ("Main", "Second"):
                    with self.subTest(sheet=name):
                        self.assertEqual(run_sheet(page, name), [str(interpreter), 72])
                self.assertEqual(config.read_bytes(), original_config)

    def test_reject_broken_and_incompatible_environments_without_changing_project_files(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            state = parent / "state"
            broken = parent / "Broken"
            incompatible = parent / "Incompatible"
            write_flow(broken, "return 1")
            write_flow(incompatible, "return 2")
            (broken / ".venv").mkdir()
            marker = broken / ".venv" / "retain.txt"
            marker.write_text("existing environment contents", encoding="utf-8")
            subprocess.run([
                "uv", "venv", "--no-project", "--python", "3.11", incompatible / ".venv",
            ], check=True)

            with application(state) as url:
                sign_in(page, url, state)
                for root, environment_file, problem in (
                    (broken, marker, "is broken"),
                    (incompatible, incompatible / ".venv" / "pyvenv.cfg", "uses Python 3.11"),
                ):
                    with self.subTest(root=root.name):
                        contents = {path: path.read_bytes() for path in (environment_file, root / "flow.py")}
                        add_workspace(page, root)
                        alert = page.get_by_role("alert")
                        expect(alert).to_contain_text(problem)
                        expect(alert).to_contain_text("Repair or move it and try again")
                        expect(page.get_by_role("link", name=root.name)).to_have_count(0)
                        for path, original in contents.items():
                            self.assertEqual(path.read_bytes(), original)
                        page.get_by_role("button", name="Cancel").click()
                self.assertEqual(subprocess.check_output([
                    project_python(incompatible), "-c", "import sys; print(sys.version_info.minor)",
                ], text=True).strip(), "11")

    def test_reject_duplicate_nested_and_application_state_folders(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            projects = parent / "projects"
            root = projects / "Registered"
            nested = root / "nested"
            nested.mkdir(parents=True)
            state_parent = parent / "application"
            state = state_parent / "state"
            state_child = state / "nested"
            state_child.mkdir(parents=True)
            alias = parent / "registered alias"
            alias.symlink_to(root, target_is_directory=True)

            with application(state) as url:
                sign_in(page, url, state)
                add_workspace(page, root)
                expect(page.get_by_role("link", name=root.name)).to_be_visible()
                for candidate, problem in (
                    (alias, "already registered"),
                    (root.with_name("registered"), "already registered"),
                    (projects, "overlaps a registered Workspace"),
                    (nested, "overlaps a registered Workspace"),
                    (state_parent, "overlaps Phyzflow application data"),
                    (state, "overlaps Phyzflow application data"),
                    (state_child, "overlaps Phyzflow application data"),
                ):
                    if not candidate.is_dir():
                        continue
                    with self.subTest(folder=candidate):
                        add_workspace(page, candidate)
                        expect(page.get_by_role("alert")).to_contain_text(problem)
                        page.get_by_role("button", name="Cancel").click()
                expect(page.get_by_role("list", name="Registered Workspaces").get_by_role("link")).to_have_count(1)
                for candidate in (projects, nested, state_parent, state, state_child):
                    self.assertFalse((candidate / ".venv").exists(), candidate)
