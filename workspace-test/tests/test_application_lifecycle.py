import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest

from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application, sign_in
from workspace_sdk.sheets import create_sheet


def create_workspace(page, url, state, root):
    sign_in(page, url, state)
    page.get_by_role("button", name="Create Workspace").click()
    page.get_by_label("New folder path").fill(str(root))
    page.get_by_role("button", name="Create", exact=True).click()
    workspace = page.get_by_role("link", name=root.name)
    expect(workspace).to_be_visible(timeout=20_000)
    return workspace


def wait_for(check):
    deadline = monotonic() + 8
    while monotonic() < deadline:
        if check():
            return
        sleep(.02)
    raise AssertionError("The expected process state was not reached.")


def process_exited(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def continuous_sheet(root, name):
    flow = create_sheet(root, name)
    (flow.parent / "nodes/start/entrypoint.py").write_text(
        r'''from pathlib import Path
import subprocess
import sys

from lifecycle_dependency import environment_note

def start():
    title = Path.cwd().name
    print(f"{title} stdout {environment_note}")
    print(f"{title} stderr", file=sys.stderr)
    Path("python").write_text(sys.executable)
    child = subprocess.Popen([sys.executable, "-c", """
import signal
from pathlib import Path
from time import sleep

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with Path("units").open("w", buffering=1) as output:
    while True:
        output.write("unit\\n")
        sleep(.03)
"""])
    Path("child-pid").write_text(str(child.pid))
    child.wait()
''',
        encoding="utf-8",
    )
    return flow.parent


class ApplicationLifecycleTests(unittest.TestCase):
    def test_run_all_preserves_the_active_run_and_starts_an_unopened_sheet(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            root = parent / "Lifecycle"
            state = parent / "state"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                create_sheet(root, "One-time")
                one_time_root = root / "flows/One-time"
                (one_time_root / "nodes/start/entrypoint.py").write_text(
                    "def start(message):\n    return message\n", encoding="utf-8",
                )
                (one_time_root / "flow.py").write_text(
                    "from workspace_sdk import Flow, Input, Output, Step\n"
                    "from nodes.start.entrypoint import start\n"
                    'flow = Flow(("message",), (Step("Start", start, {"message": Input("message")}),), Output("Start"))\n',
                    encoding="utf-8",
                )
                (one_time_root / "sample.json").write_text(
                    json.dumps({"input": {"message": "Sample input accepted"}}), encoding="utf-8",
                )
                (root / "nodes/start/entrypoint.py").write_text(
                    'from pathlib import Path\nfrom time import sleep\n\n'
                    'def start():\n    print("Main is waiting")\n'
                    '    while not Path("trigger").exists():\n        sleep(.02)\n'
                    '    return "Activation complete"\n',
                    encoding="utf-8",
                )
                workspace.click()
                workspace_url = page.url
                main = page.frame_locator('iframe[title="Main flow"]')
                main.get_by_role("button", name="Run flow").click()
                expect(main.get_by_label("stdout")).to_contain_text("Main is waiting")
                first_id = page.request.get(workspace_url + "workspace").json()["flows"][0].get("run_id")
                app_frame = page.locator('iframe[title="Main flow"]').get_attribute("src")
                duplicate = page.request.post(url + app_frame + "run", data={}, headers={"Origin": url})
                self.assertEqual(duplicate.status, 409)

                page.get_by_role("button", name="Run All").click()
                expect(page.get_by_role("tab", name="One-time")).to_contain_text("Succeeded")
                self.assertIsNotNone(first_id)
                self.assertEqual(page.request.get(workspace_url + "workspace").json()["flows"][0]["run_id"], first_id)
                page.get_by_role("tab", name="One-time").click()
                one_time = page.frame_locator('iframe[title="One-time flow"]')
                expect(one_time.locator("#result pre")).to_have_text('"Sample input accepted"')

                (one_time_root / "sample.json").write_text(
                    json.dumps({"input": {"message": "Updated sample input"}}), encoding="utf-8",
                )
                page.get_by_role("button", name="Run All").click()
                expect(one_time.locator("#result pre")).to_have_text('"Updated sample input"')

                page.goto(url)
                (root / "trigger").touch()
                page.get_by_role("link", name="Lifecycle").click()
                expect(main.locator("#result pre")).to_have_text('"Activation complete"')
                self.assertEqual(page.request.get(workspace_url + "workspace").json()["flows"][0]["run_id"], first_id)

    def test_sheet_failures_stop_and_workspace_shutdown_are_isolated(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            root = parent / "Lifecycle"
            state = parent / "state"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                python = root / ".venv/bin/python"
                site_packages = Path(subprocess.check_output(
                    [python, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True,
                ).strip())
                (site_packages / "lifecycle_dependency.py").write_text(
                    'environment_note = "Workspace dependency"\n', encoding="utf-8",
                )
                left = continuous_sheet(root, "Left")
                right = continuous_sheet(root, "Right")
                create_sheet(root, "Cannot Start").write_text("bad = (\n", encoding="utf-8")
                failure_flow = create_sheet(root, "Runtime Error")
                (failure_flow.parent / "nodes/start/entrypoint.py").write_text(
                    'from pathlib import Path\nfrom time import sleep\n\n'
                    'def start():\n    print("Runtime Error stdout")\n'
                    '    while not Path("fail-now").exists():\n        sleep(.02)\n'
                    '    raise RuntimeError("Runtime error marker")\n',
                    encoding="utf-8",
                )

                workspace.click()
                page.get_by_role("button", name="Run All").click()
                expect(page.get_by_role("tab", name="Cannot Start")).to_contain_text("Failed")
                expect(page.get_by_role("tab", name="Runtime Error")).to_contain_text("Running")
                expect(page.get_by_role("tab", name="Left")).to_contain_text("Running")
                expect(page.get_by_role("tab", name="Right")).to_contain_text("Running")
                for directory in (left, right):
                    units = directory / "units"
                    wait_for(lambda: units.is_file() and units.stat().st_size >= 15)
                    self.assertEqual((directory / "python").read_text(), str(python))
                pids = {directory.name: int((directory / "child-pid").read_text()) for directory in (left, right)}

                page.get_by_role("tab", name="Cannot Start").click()
                expect(page.locator("#notice-message")).to_contain_text("flow.py")
                page.get_by_role("tab", name="Runtime Error").click()
                failure_frame = page.frame_locator('iframe[title="Runtime Error flow"]')
                expect(failure_frame.get_by_label("stdout")).to_contain_text("Runtime Error stdout")
                (failure_flow.parent / "fail-now").touch()
                expect(failure_frame.get_by_role("heading", name="Start failed")).to_be_visible()
                expect(page.get_by_role("tab", name="Runtime Error")).to_contain_text("Failed")

                for name, other in (("Left", "Right"), ("Right", "Left")):
                    page.get_by_role("tab", name=name).click()
                    frame = page.frame_locator(f'iframe[title="{name} flow"]')
                    expect(frame.get_by_label("stdout")).to_contain_text(f"{name} stdout Workspace dependency")
                    expect(frame.get_by_label("stderr")).to_contain_text(f"{name} stderr")
                    expect(frame.get_by_label("stdout")).not_to_contain_text(f"{other} stdout")
                    expect(frame.get_by_label("stderr")).not_to_contain_text(f"{other} stderr")
                    expect(frame.get_by_label("stdout")).not_to_contain_text("Runtime Error stdout")

                page.get_by_role("button", name="Stop Left").click()
                expect(page.get_by_role("tab", name="Left")).to_contain_text("Stopped")
                wait_for(lambda: process_exited(pids["Left"]))
                expect(page.get_by_role("tab", name="Right")).to_contain_text("Running")
                right_units = right / "units"
                before = right_units.stat().st_size
                wait_for(lambda: right_units.stat().st_size > before)
            wait_for(lambda: process_exited(pids["Right"]))
