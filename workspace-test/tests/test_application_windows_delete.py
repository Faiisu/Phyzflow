from pathlib import Path
from shutil import rmtree
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application
from test_application_delete_workspace import confirm_workspace_deletion
from test_application_lifecycle import create_workspace, process_exited, wait_for


@unittest.skipUnless(sys.platform == "win32", "Windows Recycle Bin integration")
class WindowsDeleteTests(unittest.TestCase):
    def test_delete_stops_a_running_sheet_and_its_child_before_recycling(self):
        from win32com.client import Dispatch

        recycle_bin = Dispatch("Shell.Application").NameSpace(10)
        with TemporaryDirectory() as folder, browser_page() as page:
            parent = Path(folder).resolve()
            root = parent / f"Phyzflow-Delete-{uuid4().hex}"
            state = parent / "state"
            try:
                with application(state) as url:
                    workspace = create_workspace(page, url, state, root)
                    (root / "flow.py").write_text(
                        'from pathlib import Path\n'
                        'import os, subprocess, sys\n'
                        'from workspace_sdk import Flow, Output, Step\n'
                        'def run():\n'
                        '    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])\n'
                        '    Path("child-pid").write_text(str(child.pid))\n'
                        '    Path("worker-pid").write_text(str(os.getpid()))\n'
                        '    child.wait()\n'
                        'flow = Flow((), (Step("Run", run, {}),), Output("Run"))\n',
                        encoding="utf-8",
                    )
                    workspace.click()
                    page.get_by_role("button", name="Run flow").click()
                    wait_for(lambda: all((root / name).is_file() for name in ("worker-pid", "child-pid")))
                    pids = [int((root / name).read_text()) for name in ("worker-pid", "child-pid")]
                    self.assertTrue(all(not process_exited(pid) for pid in pids))
                    page.get_by_role("link", name="Workspaces").click()
                    confirm_workspace_deletion(page, root.name)
                    expect(page.get_by_role("link", name=root.name)).to_have_count(0, timeout=15_000)
                    self.assertTrue(all(process_exited(pid) for pid in pids))
                    self.assertFalse(root.exists())
                    self.assertTrue(any(item.Name == root.name for item in recycle_bin.Items()))
            finally:
                for item in recycle_bin.Items():
                    if item.Name == root.name:
                        item.InvokeVerb("undelete")
                        wait_for(root.exists)
                        rmtree(root)
                        break

    def test_delete_keeps_a_workspace_with_a_detached_process(self):
        with TemporaryDirectory() as folder, browser_page() as page:
            parent = Path(folder).resolve()
            root = parent / "Detached"
            state = parent / "state"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                script = root / "detached.py"
                script.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
                child = subprocess.Popen([sys._base_executable, script.as_posix()])
                try:
                    confirm_workspace_deletion(page, root.name)
                    expect(page.get_by_role("alert")).to_contain_text("a Workspace process is still running")
                    expect(workspace).to_be_visible()
                    self.assertTrue(root.is_dir())
                finally:
                    child.terminate()
                    child.wait(timeout=10)

    def test_recycling_an_unsupported_drive_preserves_the_folder(self):
        from workspace_sdk.windows_workspace import recycle_folder

        with TemporaryDirectory() as folder:
            drive = next((f"{letter}:" for letter in "ZYXWVUT" if not Path(f"{letter}:/").exists()), None)
            if drive is None:
                self.skipTest("No unused drive letter is available.")
            subprocess.run(["subst.exe", drive, folder], check=True)
            try:
                root = Path(f"{drive}/PhyzflowDisposableTest")
                root.mkdir()
                with self.assertRaisesRegex(OSError, "Could not move .* to the Windows Recycle Bin"):
                    recycle_folder(root)
                self.assertTrue(root.is_dir())
            finally:
                subprocess.run(["subst.exe", drive, "/d"], check=True)

    def test_delete_keeps_a_workspace_when_windows_rejects_recycling(self):
        import win32con
        import win32file

        with TemporaryDirectory() as folder, browser_page() as page:
            parent = Path(folder).resolve()
            root = parent / "Locked"
            state = parent / "state"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                handle = win32file.CreateFile(
                    str(root), win32con.GENERIC_READ,
                    win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
                    None, win32con.OPEN_EXISTING, win32con.FILE_FLAG_BACKUP_SEMANTICS, None,
                )
                try:
                    confirm_workspace_deletion(page, root.name)
                    expect(page.get_by_role("alert")).to_contain_text("to the Windows Recycle Bin")
                    expect(workspace).to_be_visible()
                    self.assertTrue((root / "flow.py").is_file())
                finally:
                    handle.Close()
                workspace.click()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Ready")
