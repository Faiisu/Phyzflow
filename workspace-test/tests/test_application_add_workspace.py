from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import unittest
from zipfile import ZipFile

from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application


def add_workspace(page, root):
    page.get_by_role("button", name="Add Workspace").click()
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
    flow.get_by_role("button", name="Run flow").click()
    expect(sheet).to_contain_text("Succeeded")
    return json.loads(flow.locator("#result pre").inner_text())


class AddWorkspaceTests(unittest.TestCase):
    def test_add_an_existing_project_and_create_its_python_312_environment(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "Existing project"
            state = parent / "state"
            source = write_flow(root, "return [sys.executable, list(sys.version_info[:2]), 'existing flow']")
            original = source.read_bytes()

            with application(state) as url:
                page.goto(url)
                add_workspace(page, root)
                workspace = page.get_by_role("link", name=root.name)
                expect(workspace).to_be_visible(timeout=20_000)
                self.assertEqual({file.name for file in root.iterdir()}, {"flow.py", ".venv"})
                workspace.click()
                self.assertEqual(run_sheet(page, "Main"), [
                    str(root / ".venv/bin/python"), [3, 12], "existing flow",
                ])
                self.assertEqual(source.read_bytes(), original)

    def test_both_sheets_reuse_the_existing_environment_and_its_installed_dependency(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "Shared dependencies"
            state = parent / "state"
            body = "import shared_sample; return [sys.executable, shared_sample.answer()]"
            write_flow(root, body)
            write_flow(root / "flows" / "Second", body)
            environment = root / ".venv"
            interpreter = environment / "bin/python"
            subprocess.run(["uv", "venv", "--no-project", "--python", "3.12", environment], check=True)
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
                page.goto(url)
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
                page.goto(url)
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
                    incompatible / ".venv" / "bin/python", "-c", "import sys; print(sys.version_info.minor)",
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
                page.goto(url)
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
