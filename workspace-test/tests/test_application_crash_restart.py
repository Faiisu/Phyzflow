import json
import os
import signal
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep

from browser_support import browser_page
from playwright.sync_api import expect
from test_application import application
from test_application_lifecycle import create_workspace, process_exited, wait_for
from workspace_sdk.sheets import create_sheet


def kill_worker(pid):
    if os.name == "nt":
        import win32api
        import win32con

        process = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
        try:
            win32api.TerminateProcess(process, 1)
        finally:
            process.Close()
    else:
        os.kill(pid, signal.SIGKILL)


class ApplicationCrashRestartTests(unittest.TestCase):
    def test_worker_crash_restarts_the_same_input_three_times_without_interrupting_other_sheets(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            root = parent / "Recovery"
            state = parent / "state"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                (root / "flow.py").write_text(
                    "from workspace_sdk import Flow, Input, Output, Step\n"
                    "from nodes.start.entrypoint import start, finish\n"
                    'flow = Flow(("input",), (Step("Start", start, {"value": Input("input")}), '
                    'Step("Finish", finish, {"value": Output("Start")})), Output("Finish"))\n',
                    encoding="utf-8",
                )
                (root / "sample.json").write_text('{"input": {"input": "Preset input"}}', encoding="utf-8")
                (root / "nodes/start/entrypoint.py").write_text(
                    r'''import json
import os
from pathlib import Path
import subprocess
import sys
from time import sleep

def start(value):
    child = subprocess.Popen([sys.executable, "-c", "from time import sleep; sleep(30)"])
    try:
        with Path("starts").open("a") as starts:
            starts.write(json.dumps([os.getpid(), value, child.pid]) + "\n")
        print(f"Entered the first step: {value}")
        while not Path("finish").exists() and not Path("fail").exists():
            sleep(.02)
        if Path("fail").exists():
            raise RuntimeError("Reported failure")
        return value
    finally:
        child.terminate()
        child.wait()

def finish(value):
    return f"Completed: {value}"
''',
                    encoding="utf-8",
                )
                other = create_sheet(root, "Other").parent
                (other / "nodes/start/entrypoint.py").write_text(
                    r'''from pathlib import Path
from time import sleep

def start():
    with Path("units").open("a", buffering=1) as output:
        while True:
            output.write("unit\n")
            sleep(.02)
''',
                    encoding="utf-8",
                )

                workspace.click()
                status_url = page.url + "workspace"
                main = page.get_by_role("tab", name="Main")
                frame = page.frame_locator('iframe[title="Main flow"]')
                frame.locator("#input").fill('{"input": "Explicit input"}')
                frame.get_by_role("button", name="Run with this input").click()
                expect(frame.get_by_label("stdout")).to_contain_text("Entered the first step: Explicit input")
                page.get_by_role("button", name="Run All").click()
                expect(page.get_by_role("tab", name="Other")).to_contain_text("Running")

                def states():
                    return {flow["name"]: flow for flow in page.request.get(status_url).json()["flows"]}

                def starts():
                    path = root / "starts"
                    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

                other_id = states()["Other"]["run_id"]
                self.assertIsNotNone(other_id)
                units = other / "units"
                wait_for(lambda: units.exists() and units.stat().st_size > 0)
                previous_units = units.stat().st_size
                attempts = set()
                for index in range(4):
                    wait_for(lambda index=index: len(starts()) > index)
                    expect(main).to_contain_text("Running")
                    if index < 3:
                        expect(frame.get_by_label("stdout")).to_contain_text("Entered the first step: Explicit input")
                    attempt = states()["Main"]["run_id"]
                    self.assertNotIn(attempt, attempts)
                    attempts.add(attempt)
                    pid, value, child_pid = starts()[index]
                    self.assertEqual(value, "Explicit input")
                    kill_worker(pid)
                    wait_for(lambda pid=pid: process_exited(pid))
                    wait_for(lambda child_pid=child_pid: process_exited(child_pid))
                    self.assertEqual(states()["Other"]["run_id"], other_id)
                    wait_for(lambda previous_units=previous_units: units.stat().st_size > previous_units)
                    previous_units = units.stat().st_size

                expect(main).to_contain_text("Failed")
                expect(page.locator("#notice-message")).to_contain_text("3 automatic restart attempts")
                sleep(.6)
                self.assertEqual(len(starts()), 4)
                self.assertEqual(states()["Main"]["run_id"], attempt)

                (root / "finish").touch()
                page.get_by_role("button", name="Reload flow").click()
                frame.locator("#input").fill('{"input": "New input"}')
                frame.get_by_role("button", name="Run with this input").click()
                expect(frame.locator("#result pre")).to_have_text('"Completed: New input"')
                expect(main).to_contain_text("Succeeded")
                self.assertEqual(len(starts()), 5)

                (root / "finish").unlink()
                (root / "fail").touch()
                page.get_by_role("button", name="Run flow").click()
                expect(frame.get_by_role("heading", name="Start failed")).to_be_visible()
                expect(main).to_contain_text("Failed")
                failed_id = states()["Main"]["run_id"]
                sleep(.6)
                self.assertEqual(len(starts()), 6)
                self.assertEqual(states()["Main"]["run_id"], failed_id)

                (root / "fail").unlink()
                page.get_by_role("button", name="Run flow").click()
                wait_for(lambda: len(starts()) == 7)
                running_id = states()["Main"]["run_id"]
                self.assertNotEqual(running_id, failed_id)
                page.get_by_role("button", name="Stop Main").click()
                expect(main).to_contain_text("Stopped")
                wait_for(lambda: process_exited(starts()[-1][0]))
                wait_for(lambda: process_exited(starts()[-1][2]))
                sleep(.6)
                self.assertEqual(len(starts()), 7)
                self.assertEqual(states()["Main"]["run_id"], running_id)
                self.assertEqual(states()["Other"]["run_id"], other_id)
