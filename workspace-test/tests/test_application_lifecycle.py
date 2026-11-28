import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from browser_support import browser_page
from playwright.sync_api import expect
from test_application import application, sign_in
from workspace_sdk.sheets import create_sheet


def create_workspace(page, url, state, root):
    sign_in(page, url, state)
    page.get_by_role("button", name="Create Workspace").click()
    page.get_by_role("button", name="Enter a folder path").click()
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
    if os.name == "nt":
        import pywintypes
        import win32api
        import win32con
        import win32event

        try:
            handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid)
        except pywintypes.error as error:
            if error.winerror == 87:
                return True
            raise
        try:
            return win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_OBJECT_0
        finally:
            handle.Close()
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
    def test_narrow_workspace_keeps_sheet_tabs_actions_and_flow_panels_reachable(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            root = parent / ("Engineering Workspace " * 4).strip()
            with application(parent / "state") as url:
                workspace = create_workspace(page, url, parent / "state", root)
                for name in ("Quality checks", "Results dashboard", "Telemetry pipeline"):
                    create_sheet(root, name)
                page.set_viewport_size({"width": 320, "height": 620})
                workspace.click()

                main = page.get_by_role("tab", name="Main")
                status = page.locator("#workspace-flow-status")
                expect(status).to_have_text("Ready")
                expect(main.locator(".sheet-status")).to_be_hidden()
                expect(main).to_have_accessible_description("Ready")
                expect(page.get_by_role("tab", name="Quality checks").locator(".sheet-status")).to_be_visible()
                expect(page.get_by_role("button", name="Stop Main")).to_have_count(0)
                flow = page.frame_locator('iframe[title="Main flow"]')
                inspector = flow.get_by_role("region", name="Node inspector")
                header = page.locator(".workspace-header")
                for width, height in ((320, 620), (390, 844), (768, 1024), (1440, 900), (320, 620)):
                    page.set_viewport_size({"width": width, "height": height})
                    with self.subTest(width=width):
                        self.assertTrue(page.locator("html").evaluate("root => root.scrollWidth === root.clientWidth"))
                        self.assertGreater(page.get_by_role("tablist", name="Flows").bounding_box()["width"], 200)
                        for action in ("Run flow", "Run All", "Rename sheet", "Edit flow.py", "Reload flow", "New sheet"):
                            button = page.get_by_role("button", name=action, exact=True).bounding_box()
                            self.assertLessEqual(button["x"] + button["width"], width)
                        if width == 1440:
                            header_box = header.bounding_box()
                            for panel in (flow.locator("#panel-outline"), inspector):
                                expect(panel).to_be_visible()
                                self.assertAlmostEqual(
                                    panel.bounding_box()["y"] - header_box["y"] - header_box["height"], 13.6, delta=1,
                                )

                run = flow.get_by_role("region", name="Run", exact=True)
                expect(flow.locator("#panel-toolbar")).to_have_count(0)
                expect(flow.locator('[aria-controls="panel-toolbar"]')).to_have_count(0)
                expect(inspector).to_be_hidden()
                expect(run).to_be_hidden()
                flow.get_by_role("button", name="Inspect Start").click()
                expect(inspector).to_be_visible()
                header_box = header.bounding_box()
                self.assertAlmostEqual(
                    inspector.bounding_box()["y"] - header_box["y"] - header_box["height"], 8, delta=1,
                )
                run_flow = page.get_by_role("button", name="Run flow", exact=True)
                run_flow.click()
                expect(status).to_have_text("Succeeded")
                expect(inspector).to_be_hidden()
                expect(run).to_be_visible()
                run_input = run.get_by_role("textbox", name="Run input JSON")
                run_input.fill("[]")
                run_flow.click()
                expect(status).to_have_text("Failed")
                expect(main).to_have_accessible_description("Failed")
                run_input.fill("{}")
                run_flow.click()
                expect(main).to_contain_text("Succeeded")
                expect(status).to_have_text("Succeeded")

                main.focus()
                main.press("End")
                telemetry = page.get_by_role("tab", name="Telemetry pipeline")
                expect(telemetry).to_be_focused()
                expect(telemetry).to_have_attribute("aria-selected", "true")
                expect(telemetry).to_be_in_viewport(ratio=1)
                expect(main.locator(".sheet-status")).to_be_visible()
                expect(telemetry.locator(".sheet-status")).to_be_hidden()
                page.get_by_role("button", name="New sheet").click()
                sheet = page.get_by_role("dialog", name="New sheet")
                sheet.get_by_label("Sheet name").fill("Mobile follow-up")
                sheet.get_by_role("button", name="Create sheet").click()
                expect(page.get_by_role("tab", name="Mobile follow-up")).to_be_focused()

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
                page.get_by_role("button", name="Run flow").click()
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
                    wait_for(lambda units=units: units.is_file() and units.stat().st_size >= 15)
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

                page.get_by_role("tab", name="Left").click()
                left_frame = page.frame_locator('iframe[title="Left flow"]')
                status = page.locator("#workspace-flow-status")
                expect(left_frame.locator(".node .state")).to_have_text("Running")
                flows = page.request.get(f"{page.url}workspace").json()["flows"]
                left_index = next(index for index, flow in enumerate(flows) if flow["name"] == "Left")
                rejected = page.request.post(
                    f"{page.url}flows/{left_index}/recovery/node-source/0",
                    headers={"Origin": url},
                    data={"path": "nodes/start/entrypoint.py", "source": "def start(): pass", "revision": "0" * 64},
                )
                self.assertEqual(rejected.status, 409)
                self.assertIn("while the flow is running", rejected.json()["error"])
                stop_requests = []
                page.route("**/flows/*/stop", lambda route: stop_requests.append(route))
                with page.expect_request("**/flows/*/stop"):
                    page.get_by_role("button", name="Stop Left").click()
                expect(status).to_have_text("Stopping…")
                expect(page.get_by_role("button", name="Stop Left")).to_be_disabled()
                stop_requests[0].continue_()
                expect(page.get_by_role("tab", name="Left")).to_contain_text("Stopped")
                expect(status).to_have_text("Stopped")
                expect(page.get_by_role("button", name="Stop Left")).to_have_count(0)
                expect(left_frame.locator(".node .state")).to_have_text("Stopped")
                wait_for(lambda: process_exited(pids["Left"]))
                expect(page.get_by_role("tab", name="Right")).to_contain_text("Running")
                right_units = right / "units"
                before = right_units.stat().st_size
                wait_for(lambda: right_units.stat().st_size > before)

                expect(page.get_by_role("button", name="Open flow", exact=True)).to_be_hidden()
                left_frame.get_by_role("button", name="Inspect Start").dblclick()
                editor = left_frame.get_by_role("dialog", name="Start files")
                expect(editor.locator("#node-source-name")).to_have_text("nodes/start/settings.json")
                source = editor.get_by_role("textbox", name="Source code")
                original_settings = source.input_value()
                source.fill("[]")
                editor.get_by_role("button", name="Save", exact=True).click()
                expect(editor.locator("#node-source-message")).to_have_text("Settings defaults must be a JSON object.")
                source.fill(original_settings)
                editor.get_by_role("button", name="entrypoint.py", exact=True).click()
                expect(editor.locator("#node-source-name")).to_have_text("nodes/start/entrypoint.py")
                source.fill('def start():\n    return "Edited after stopping"\n')
                editor.get_by_role("button", name="Save", exact=True).click()
                expect(editor.locator("#node-source-message")).to_contain_text("Saved.")
                editor.get_by_role("button", name="Close node files").click()
                expect(status).to_have_text("Stopped")
                state = page.request.get(f"{page.url}workspace").json()
                self.assertFalse(next(flow for flow in state["flows"] if flow["name"] == "Left")["worker_ready"])

                page.get_by_role("button", name="Run flow", exact=True).click()
                expect(status).to_have_text("Succeeded")
                expect(left_frame.locator("#result pre")).to_have_text('"Edited after stopping"')
            wait_for(lambda: process_exited(pids["Right"]))
