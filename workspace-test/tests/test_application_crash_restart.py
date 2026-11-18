import json
import os
from pathlib import Path
import signal
from tempfile import TemporaryDirectory
from time import sleep
import unittest

from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application
from test_application_lifecycle import create_workspace, process_exited, wait_for
from workspace_sdk.sheets import create_sheet


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
from time import sleep

def start(value):
    with Path("starts").open("a") as starts:
        starts.write(json.dumps([os.getpid(), value]) + "\n")
    print(f"Entered the first step: {value}")
    while not Path("finish").exists() and not Path("fail").exists():
        sleep(.02)
    if Path("fail").exists():
        raise RuntimeError("Reported failure")
    return value

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
                    wait_for(lambda: len(starts()) > index)
                    expect(main).to_contain_text("Running")
                    if index < 3:
                        expect(frame.get_by_label("stdout")).to_contain_text("Entered the first step: Explicit input")
                    attempt = states()["Main"]["run_id"]
                    self.assertNotIn(attempt, attempts)
                    attempts.add(attempt)
                    pid, value = starts()[index]
                    self.assertEqual(value, "Explicit input")
                    os.kill(pid, signal.SIGKILL)
                    wait_for(lambda: process_exited(pid))
                    self.assertEqual(states()["Other"]["run_id"], other_id)
                    wait_for(lambda: units.stat().st_size > previous_units)
                    previous_units = units.stat().st_size

                expect(main).to_contain_text("Failed")
                expect(page.locator("#notice-message")).to_contain_text("3 automatic restart attempts")
                sleep(.6)
                self.assertEqual(len(starts()), 4)
                self.assertEqual(states()["Main"]["run_id"], attempt)

                (root / "finish").touch()
                page.get_by_role("button", name="Open flow").click()
                expect(main).to_contain_text("Failed")
                frame.locator("#input").fill('{"input": "New input"}')
                frame.get_by_role("button", name="Run with this input").click()
                expect(frame.locator("#result pre")).to_have_text('"Completed: New input"')
                expect(main).to_contain_text("Succeeded")
                self.assertEqual(len(starts()), 5)

                (root / "finish").unlink()
                (root / "fail").touch()
                frame.get_by_role("button", name="Run flow").click()
                expect(frame.get_by_role("heading", name="Start failed")).to_be_visible()
                expect(main).to_contain_text("Failed")
                failed_id = states()["Main"]["run_id"]
                sleep(.6)
                self.assertEqual(len(starts()), 6)
                self.assertEqual(states()["Main"]["run_id"], failed_id)

                (root / "fail").unlink()
                frame.get_by_role("button", name="Run flow").click()
                wait_for(lambda: len(starts()) == 7)
                running_id = states()["Main"]["run_id"]
                self.assertNotEqual(running_id, failed_id)
                page.get_by_role("button", name="Stop Main").click()
                expect(main).to_contain_text("Stopped")
                wait_for(lambda: process_exited(starts()[-1][0]))
                sleep(.6)
                self.assertEqual(len(starts()), 7)
                self.assertEqual(states()["Main"]["run_id"], running_id)
                self.assertEqual(states()["Other"]["run_id"], other_id)
