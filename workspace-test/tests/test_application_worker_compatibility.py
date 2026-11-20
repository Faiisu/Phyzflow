from importlib.metadata import version
import os
from pathlib import Path
from shutil import copytree, ignore_patterns
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application, project_python, sign_in
from test_application_add_workspace import add_workspace, run_sheet, write_flow
import workspace_sdk


def application_copy(parent):
    app_install = parent / "application-install"
    package = app_install / "workspace_sdk"
    copytree(Path(workspace_sdk.__file__).parent, package, ignore=ignore_patterns("__pycache__"))
    sdk_version = version("workspace-sdk")
    metadata = app_install / f"workspace_sdk-{sdk_version}.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: workspace-sdk\nVersion: {sdk_version}\n",
        encoding="utf-8",
    )
    return app_install, package / "sheet_worker.py"


class ApplicationWorkerCompatibilityTests(unittest.TestCase):
    def test_supported_worker_opens_and_reports_sdk_when_workspace_has_no_distribution(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            root, state = parent / "Project", parent / "state"
            write_flow(root, "return 72")
            with application(state) as url:
                sign_in(page, url, state)
                add_workspace(page, root)
                page.get_by_role("link", name=root.name, exact=True).click(timeout=20_000)
                self.assertEqual(run_sheet(page, "Main"), 72)
                startup = page.request.get(f"{page.url}workspace").json()["flows"][0]["startup"]
                self.assertEqual(startup["protocol_version"], 1)
                self.assertEqual(startup["supported_protocol_versions"], [1])
                self.assertEqual(startup["loaded_sdk_version"], version("workspace-sdk"))
                self.assertIsNone(startup["installed_sdk_version"])

    def test_worker_loads_application_sdk_even_when_workspace_has_a_different_distribution(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            root, state = parent / "Project", parent / "state"
            write_flow(root, "return 72")
            interpreter = project_python(root)
            managed_python = ["--managed-python"] if os.name == "nt" else []
            subprocess.run([
                "uv", "venv", "--no-project", *managed_python, "--python", "3.12", root / ".venv",
            ], check=True)
            wheel = parent / "workspace_sdk-0.1.0-py3-none-any.whl"
            with ZipFile(wheel, "w") as archive:
                archive.writestr("workspace_sdk/__init__.py", 'raise RuntimeError("The Workspace SDK was loaded")\n')
                archive.writestr(
                    "workspace_sdk-0.1.0.dist-info/METADATA",
                    "Metadata-Version: 2.1\nName: workspace-sdk\nVersion: 0.1.0\n",
                )
                archive.writestr(
                    "workspace_sdk-0.1.0.dist-info/WHEEL",
                    "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                )
                archive.writestr("workspace_sdk-0.1.0.dist-info/RECORD", "")
            subprocess.run(["uv", "pip", "install", "--offline", "--python", interpreter, wheel], check=True)
            with application(state) as url:
                sign_in(page, url, state)
                add_workspace(page, root)
                page.get_by_role("link", name=root.name, exact=True).click(timeout=20_000)
                self.assertEqual(run_sheet(page, "Main"), 72)
                startup = page.request.get(f"{page.url}workspace").json()["flows"][0]["startup"]
                self.assertEqual(startup["loaded_sdk_version"], version("workspace-sdk"))
                self.assertEqual(startup["installed_sdk_version"], "0.1.0")

    def test_unsupported_or_missing_protocol_is_diagnosed_before_loading_project(self):
        for protocol in (99, None):
            with self.subTest(combination=protocol), TemporaryDirectory() as temporary, browser_page() as page:
                parent = Path(temporary).resolve()
                root, state = parent / "Project", parent / "state"
                source = write_flow(root, "return 72")
                marker = root / "flow-imported"
                source.write_text(
                    f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + source.read_text(),
                    encoding="utf-8",
                )
                app_install, worker = application_copy(parent)
                sdk_version = version("workspace-sdk")
                announcement = '"protocol_version": SHEET_WORKER_PROTOCOL_VERSION,'
                replacement = f'"protocol_version": {protocol},' if protocol is not None else ""
                worker_source = worker.read_text()
                self.assertIn(announcement, worker_source)
                worker.write_text(worker_source.replace(announcement, replacement), encoding="utf-8")
                with patch.dict(os.environ, PYTHONPATH=str(app_install)), application(state) as url:
                    sign_in(page, url, state)
                    add_workspace(page, root)
                    page.get_by_role("link", name=root.name, exact=True).click(timeout=20_000)
                    error = (
                        f"Unsupported Sheet Worker protocol {protocol}. Supported versions: 1."
                        if protocol is not None else "Sheet Worker protocol is missing. Supported versions: 1."
                    )
                    expect(page.get_by_text(error, exact=True)).to_be_visible()
                    startup = page.request.get(f"{page.url}workspace").json()["flows"][0]["startup"]
                    self.assertEqual(startup["stage"], "startup")
                    self.assertEqual(startup["protocol_version"], protocol)
                    self.assertEqual(startup["supported_protocol_versions"], [1])
                    self.assertEqual(startup["loaded_sdk_version"], sdk_version)
                    self.assertIsNone(startup["installed_sdk_version"])
                    self.assertEqual(startup["error"], error)
                    self.assertFalse(marker.exists())


    def test_worker_bootstrap_failures_preserve_the_startup_reason(self):
        for worker_code, reason in (
            ('raise SystemExit(3)\n', "exited during startup"),
            ('from pathlib import Path\nimport sys\nPath(sys.argv[2]).write_text("invalid")\n', "invalid JSON"),
            ('from time import sleep\nsleep(30)\n', "did not start within ten seconds"),
        ):
            with self.subTest(reason=reason), TemporaryDirectory() as temporary, browser_page() as page:
                parent = Path(temporary).resolve()
                root, state = parent / "Project", parent / "state"
                source = write_flow(root, "return 72")
                marker = root / "flow-imported"
                source.write_text(
                    f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + source.read_text(),
                    encoding="utf-8",
                )
                app_install, worker = application_copy(parent)
                worker.write_text(worker_code, encoding="utf-8")
                with patch.dict(os.environ, PYTHONPATH=str(app_install)), application(state) as url:
                    sign_in(page, url, state)
                    add_workspace(page, root)
                    page.get_by_role("link", name=root.name, exact=True).click(timeout=20_000)
                    expect(page.get_by_text(reason, exact=False)).to_be_visible(timeout=20_000)
                    startup = page.request.get(f"{page.url}workspace").json()["flows"][0]["startup"]
                    self.assertEqual(startup["stage"], "startup")
                    self.assertIsNone(startup["protocol_version"])
                    self.assertEqual(startup["supported_protocol_versions"], [1])
                    self.assertIn("Sheet Worker protocol is missing", startup["error"])
                    self.assertIn(reason, startup["error"])
                    self.assertFalse(marker.exists())
