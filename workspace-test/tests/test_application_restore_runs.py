from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
from stat import S_IMODE
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import sleep
import unittest

from playwright.sync_api import expect

from browser_support import browser_page
from test_application import read_application_line, sign_in
from test_application_crash_restart import kill_worker
from test_application_lifecycle import create_workspace, process_exited, wait_for
from workspace_sdk.history import RunHistory
from workspace_sdk.runner import Flow, Step, Output, run
from workspace_sdk.sheet_runs import RunStateStore
from workspace_sdk.sheets import create_sheet, discover_flows, initialize_workspace
from workspace_sdk.ui_settings import load_ui_settings
from workspace_sdk.workspace_ui import workspace_routes


@contextmanager
def managed_application(state: Path, *, shutdown_signal: int | None = signal.SIGINT):
    windows = os.name == "nt"
    cli = [Path(sys.executable).with_name("workspace")]
    if windows:
        cli = [
            sys.executable, "-c",
            "import os; print(os.getpid(), flush=True); from workspace_sdk.cli import main; raise SystemExit(main())",
        ]
    with subprocess.Popen(
        [*cli, "app", "--state-dir", state, "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        actual_pid = None
        try:
            line = read_application_line(process.stdout)
            if windows:
                actual_pid = int(line)
                line = read_application_line(process.stdout)
            prefix = "Phyzflow: "
            if not line.startswith(prefix):
                raise RuntimeError(f"Phyzflow did not publish its address: {line!r}.")
            yield line.removeprefix(prefix), process
        finally:
            if process.poll() is None:
                if windows and actual_pid is not None:
                    kill_worker(actual_pid)
                elif windows or shutdown_signal is None:
                    process.kill()
                else:
                    process.send_signal(shutdown_signal)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def read_lines(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_restorable_workspace_sheets(root: Path) -> dict[str, Path]:
    (root / "flow.py").write_text(
        "from workspace_sdk import Flow, Input, Output, Step\n"
        "from nodes.start.entrypoint import start, finish\n"
        'flow = Flow(("value",), (Step("Start", start, {"value": Input("value")}), '
        'Step("Finish", finish, {"value": Output("Start")})), Output("Finish"))\n',
        encoding="utf-8",
    )
    (root / "sample.json").write_text('{"input": {"value": "Sample fallback"}}', encoding="utf-8")
    (root / "nodes/start/entrypoint.py").write_text(
        '''import json
import os
from pathlib import Path
from time import sleep

def start(value):
    with Path("starts").open("a", encoding="utf-8") as log:
        log.write(json.dumps([os.getpid(), value]) + "\\n")
    print(f"OneTime started: {value}", flush=True)
    return value

def finish(value):
    with Path("finish_starts").open("a", encoding="utf-8") as log:
        log.write(json.dumps([os.getpid(), value]) + "\\n")
    while not Path("finish").exists():
        sleep(.02)
    Path("completed").write_text(json.dumps(value), encoding="utf-8")
    return f"Done: {value}"
''',
        encoding="utf-8",
    )

    automation = create_sheet(root, "Automation").parent
    (automation / "nodes/start/entrypoint.py").write_text(
        '''import json
import os
from pathlib import Path
from time import sleep

def start():
    with Path("starts").open("a", encoding="utf-8") as log:
        log.write(json.dumps([os.getpid()]) + "\\n")
    print("Automation waiting in Python", flush=True)
    while not Path("trigger").exists():
        sleep(.02)
    Path("completed").write_text(str(os.getpid()), encoding="utf-8")
    return "Triggered"
''',
        encoding="utf-8",
    )

    continuous = create_sheet(root, "Continuous").parent
    (continuous / "nodes/start/entrypoint.py").write_text(
        '''import json
import os
from pathlib import Path
import subprocess
import sys
from time import sleep

def start():
    child = subprocess.Popen([sys.executable, "-c", "from time import sleep; sleep(60)"])
    Path("child-pid").write_text(str(child.pid))
    with Path("starts").open("a", encoding="utf-8") as log:
        log.write(json.dumps([os.getpid()]) + "\\n")
    print("Continuous running", flush=True)
    with Path("units").open("a", buffering=1, encoding="utf-8") as output:
        while True:
            output.write(f"{os.getpid()}\\n")
            sleep(.03)
''',
        encoding="utf-8",
    )
    return {"Main": root, "Automation": automation, "Continuous": continuous}


class ApplicationRestoreRunsTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "SIGTERM application shutdown is a POSIX scenario.")
    def test_restores_active_onetime_automation_and_continuous_runs_on_sigterm_restart_without_browser(self):
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "Restorable"
            state = parent / "state"

            with browser_page() as page:
                with managed_application(state, shutdown_signal=signal.SIGTERM) as (url, _):
                    workspace = create_workspace(page, url, state, root)
                    sheets = write_restorable_workspace_sheets(root)
                    workspace.click()
                    status_url = page.url + "workspace"

                    main_frame = page.frame_locator('iframe[title="Main flow"]')
                    main_frame.locator("#input").fill('{"value": "Custom input"}')
                    main_frame.get_by_role("button", name="Run with this input").click()
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Running")
                    page.get_by_role("button", name="Run All").click()
                    expect(page.get_by_role("tab", name="Automation")).to_contain_text("Running")
                    expect(page.get_by_role("tab", name="Continuous")).to_contain_text("Running")

                    for name, directory in sheets.items():
                        wait_for(lambda d=directory: len(read_lines(d / "starts")) == 1)
                    wait_for(lambda: len(read_lines(sheets["Main"] / "finish_starts")) == 1)
                    wait_for(lambda: (sheets["Continuous"] / "units").exists())
                    before_states = {flow["name"]: flow for flow in page.request.get(status_url).json()["flows"]}
                    first_pids = {name: read_lines(directory / "starts")[0][0] for name, directory in sheets.items()}

                for pid in first_pids.values():
                    wait_for(lambda p=pid: process_exited(p))

            units_before = (sheets["Continuous"] / "units").stat().st_size

            with managed_application(state, shutdown_signal=signal.SIGINT) as (url, _):
                for name, directory in sheets.items():
                    wait_for(lambda d=directory: len(read_lines(d / "starts")) == 2)
                wait_for(lambda: len(read_lines(sheets["Main"] / "finish_starts")) == 2)
                self.assertEqual(read_lines(sheets["Main"] / "starts")[1][1], "Custom input")
                self.assertFalse((sheets["Automation"] / "completed").exists())
                self.assertFalse((sheets["Main"] / "completed").exists())
                wait_for(lambda: (sheets["Continuous"] / "units").stat().st_size > units_before)
                second_pids = {name: read_lines(directory / "starts")[1][0] for name, directory in sheets.items()}
                for name in sheets:
                    self.assertNotEqual(second_pids[name], first_pids[name])

                with browser_page() as page:
                    sign_in(page, url, state)
                    page.get_by_role("link", name="Restorable").click()
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Running")
                    expect(page.get_by_role("tab", name="Automation")).to_contain_text("Running")
                    expect(page.get_by_role("tab", name="Continuous")).to_contain_text("Running")
                    after_states = {flow["name"]: flow for flow in page.request.get(page.url + "workspace").json()["flows"]}
                    for name in sheets:
                        self.assertNotEqual(after_states[name]["run_id"], before_states[name]["run_id"])

                    main_frame = page.frame_locator('iframe[title="Main flow"]')
                    expect(main_frame.get_by_label("stdout")).to_contain_text("OneTime started: Custom input")
                    (sheets["Main"] / "finish").touch()
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                    self.assertEqual(json.loads((sheets["Main"] / "completed").read_text()), "Custom input")

                    page.get_by_role("tab", name="Automation").click()
                    auto_frame = page.frame_locator('iframe[title="Automation flow"]')
                    expect(auto_frame.get_by_label("stdout")).to_contain_text("Automation waiting in Python")
                    self.assertFalse((sheets["Automation"] / "completed").exists())
                    (sheets["Automation"] / "trigger").touch()
                    expect(page.get_by_role("tab", name="Automation")).to_contain_text("Succeeded")
                    self.assertEqual(int((sheets["Automation"] / "completed").read_text()), second_pids["Automation"])

                    page.get_by_role("tab", name="Continuous").click()
                    cont_frame = page.frame_locator('iframe[title="Continuous flow"]')
                    expect(cont_frame.get_by_label("stdout")).to_contain_text("Continuous running")
                    page.get_by_role("button", name="Stop Continuous").click()
                    expect(page.get_by_role("tab", name="Continuous")).to_contain_text("Stopped")
                    wait_for(lambda: process_exited(second_pids["Continuous"]))

    def test_restores_active_runs_after_abrupt_termination_without_browser(self):
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "Abrupt"
            state = parent / "state"

            with browser_page() as page:
                with managed_application(state, shutdown_signal=None) as (url, _):
                    workspace = create_workspace(page, url, state, root)
                    sheets = write_restorable_workspace_sheets(root)
                    workspace.click()
                    status_url = page.url + "workspace"

                    main_frame = page.frame_locator('iframe[title="Main flow"]')
                    main_frame.locator("#input").fill('{"value": "Survives sigkill"}')
                    main_frame.get_by_role("button", name="Run with this input").click()
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Running")
                    page.get_by_role("button", name="Run All").click()

                    for directory in sheets.values():
                        wait_for(lambda d=directory: len(read_lines(d / "starts")) == 1)
                    wait_for(lambda: (sheets["Continuous"] / "units").exists())
                    before_states = {flow["name"]: flow for flow in page.request.get(status_url).json()["flows"]}
                    first_pids = {name: read_lines(directory / "starts")[0][0] for name, directory in sheets.items()}
                    child_pid = int((sheets["Continuous"] / "child-pid").read_text())

            with managed_application(state, shutdown_signal=signal.SIGINT) as (url, _):
                for pid in first_pids.values():
                    wait_for(lambda p=pid: process_exited(p))
                wait_for(lambda: process_exited(child_pid))
                for directory in sheets.values():
                    wait_for(lambda d=directory: len(read_lines(d / "starts")) == 2)
                self.assertEqual(read_lines(sheets["Main"] / "starts")[1][1], "Survives sigkill")
                self.assertFalse((sheets["Automation"] / "completed").exists())
                second_pids = {name: read_lines(directory / "starts")[1][0] for name, directory in sheets.items()}
                for name in sheets:
                    self.assertNotEqual(second_pids[name], first_pids[name])
                wait_for(
                    lambda: str(second_pids["Continuous"]) in (sheets["Continuous"] / "units").read_text(encoding="utf-8").splitlines(),
                )

                with browser_page() as page:
                    sign_in(page, url, state)
                    page.get_by_role("link", name="Abrupt").click()
                    after_states = {flow["name"]: flow for flow in page.request.get(page.url + "workspace").json()["flows"]}
                    for name in sheets:
                        self.assertNotEqual(after_states[name]["run_id"], before_states[name]["run_id"])
                    (sheets["Main"] / "finish").touch()
                    (sheets["Automation"] / "trigger").touch()
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                    expect(page.get_by_role("tab", name="Automation")).to_contain_text("Succeeded")

    def test_run_state_store_reconciles_terminal_history_in_acknowledgment_window_and_protects_storage(self):
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "Reconcile"
            state = parent / "state"
            initialize_workspace(root, with_main=True)
            (root / "nodes/start/entrypoint.py").write_text(
                'from pathlib import Path\n'
                'def start():\n'
                '    Path("started").write_text("ran", encoding="utf-8")\n'
                '    return "ok"\n',
                encoding="utf-8",
            )
            store = RunStateStore(state)
            try:
                if os.name == "posix":
                    self.assertEqual(S_IMODE(store.path.stat().st_mode), 0o600)
                run_id = "0123456789abcdef0123456789abcdef"
                events = []

                def step_fn():
                    return "ok"

                result = run(Flow((), (Step("Start", step_fn, {}),), Output("Start")), {}, events.append, include_metadata=True, run_id=run_id)
                self.assertEqual(result.status, "succeeded")
                RunHistory(root).save(events, {}, 1.0)
                store.record_running("ws1", ".", 3, run_id, {})
                store.record_terminal("ws1", ".", 2, "ffffffffffffffffffffffffffffffff", "failed", "stale")
                self.assertEqual(store.workspace_states("ws1")["."]["status"], "running")

                _, close, resolve = workspace_routes(
                    root,
                    discover_flows(root),
                    load_ui_settings(root, None),
                    interpreter=Path(sys.executable),
                    workspace_id="ws1",
                    run_state=store,
                )
                try:
                    sheet = resolve(root)
                    self.assertEqual(sheet.status(), "succeeded")
                    self.assertEqual(sheet.attempt.run_id, run_id)
                    self.assertEqual(store.workspace_states("ws1")["."]["status"], "succeeded")
                    self.assertFalse((root / "started").exists())
                finally:
                    close()

                self.assertEqual(store.active_workspace_ids(), ["ws1"])
                store.delete_workspace("ws1")
                self.assertEqual(store.active_workspace_ids(), [])
            finally:
                store.close()

    def test_preserves_stopped_and_terminal_states_without_replay_and_displays_them_in_ui(self):
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "Mixed"
            state = parent / "state"

            with browser_page() as page:
                with managed_application(state, shutdown_signal=signal.SIGINT) as (url, _):
                    workspace = create_workspace(page, url, state, root)

                    (root / "nodes/start/entrypoint.py").write_text(
                        '''import json
import os
from pathlib import Path
from time import sleep

def start():
    with Path("starts").open("a", encoding="utf-8") as log:
        log.write(json.dumps([os.getpid()]) + "\\n")
    print("Active sheet running", flush=True)
    while not Path("finish").exists():
        sleep(.02)
    return "Active done"
''',
                        encoding="utf-8",
                    )

                    stopped_dir = create_sheet(root, "Manual").parent
                    (stopped_dir / "nodes/start/entrypoint.py").write_text(
                        '''import json
import os
from pathlib import Path
from time import sleep

def start():
    with Path("starts").open("a", encoding="utf-8") as log:
        log.write(json.dumps([os.getpid()]) + "\\n")
    while True:
        sleep(.03)
''',
                        encoding="utf-8",
                    )

                    succeeded_dir = create_sheet(root, "Completed").parent
                    (succeeded_dir / "nodes/start/entrypoint.py").write_text(
                        '''import json
import os
from pathlib import Path

def start():
    with Path("starts").open("a", encoding="utf-8") as log:
        log.write(json.dumps([os.getpid()]) + "\\n")
    return "Finished cleanly"
''',
                        encoding="utf-8",
                    )

                    failed_step_dir = create_sheet(root, "StepError").parent
                    (failed_step_dir / "nodes/start/entrypoint.py").write_text(
                        '''import json
import os
from pathlib import Path

def start():
    with Path("starts").open("a", encoding="utf-8") as log:
        log.write(json.dumps([os.getpid()]) + "\\n")
    raise RuntimeError("Boom")
''',
                        encoding="utf-8",
                    )

                    failed_startup_flow = create_sheet(root, "StartupError")
                    failed_startup_flow.write_text("broken = (\n", encoding="utf-8")

                    workspace.click()
                    status_url = page.url + "workspace"
                    page.get_by_role("button", name="Run All").click()

                    expect(page.get_by_role("tab", name="Main").locator(".sheet-status")).to_have_text("Running")
                    expect(page.get_by_role("tab", name="Manual").locator(".sheet-status")).to_have_text("Running")
                    expect(page.get_by_role("tab", name="Completed").locator(".sheet-status")).to_have_text("Succeeded")
                    expect(page.get_by_role("tab", name="StepError").locator(".sheet-status")).to_have_text("Failed")
                    expect(page.get_by_role("tab", name="StartupError").locator(".sheet-status")).to_have_text("Failed")

                    wait_for(lambda: len(read_lines(root / "starts")) == 1)
                    wait_for(lambda: len(read_lines(stopped_dir / "starts")) == 1)
                    stopped_pid = read_lines(stopped_dir / "starts")[0][0]
                    page.get_by_role("button", name="Stop Manual").click()
                    expect(page.get_by_role("tab", name="Manual").locator(".sheet-status")).to_have_text("Stopped")
                    wait_for(lambda: process_exited(stopped_pid))

                    before_states = {flow["name"]: flow for flow in page.request.get(status_url).json()["flows"]}
                    active_pid_before = read_lines(root / "starts")[0][0]

                wait_for(lambda: process_exited(active_pid_before))

            with managed_application(state, shutdown_signal=signal.SIGINT) as (url, _):
                wait_for(lambda: len(read_lines(root / "starts")) == 2)
                sleep(.6)
                self.assertEqual(len(read_lines(stopped_dir / "starts")), 1)
                self.assertEqual(len(read_lines(succeeded_dir / "starts")), 1)
                self.assertEqual(len(read_lines(failed_step_dir / "starts")), 1)
                active_pid_after = read_lines(root / "starts")[1][0]
                self.assertNotEqual(active_pid_after, active_pid_before)

                with browser_page() as page:
                    sign_in(page, url, state)
                    page.get_by_role("link", name="Mixed").click()
                    after_states = {flow["name"]: flow for flow in page.request.get(page.url + "workspace").json()["flows"]}
                    self.assertNotEqual(after_states["Main"]["run_id"], before_states["Main"]["run_id"])
                    self.assertEqual(after_states["Completed"]["run_id"], before_states["Completed"]["run_id"])
                    self.assertEqual(after_states["StepError"]["run_id"], before_states["StepError"]["run_id"])
                    self.assertEqual(after_states["StartupError"]["run_id"], before_states["StartupError"]["run_id"])

                    expect(page.get_by_role("tab", name="Main").locator(".sheet-status")).to_have_text("Running")
                    expect(page.frame_locator('iframe[title="Main flow"]').get_by_label("stdout")).to_contain_text("Active sheet running")

                    page.get_by_role("tab", name="Completed").click()
                    expect(page.locator('iframe[title="Completed flow"]')).to_be_visible()
                    sleep(.4)
                    expect(page.get_by_role("tab", name="Completed").locator(".sheet-status")).to_have_text("Succeeded")
                    self.assertEqual(len(read_lines(succeeded_dir / "starts")), 1)

                    page.get_by_role("tab", name="StepError").click()
                    expect(page.locator('iframe[title="StepError flow"]')).to_be_visible()
                    sleep(.4)
                    expect(page.get_by_role("tab", name="StepError").locator(".sheet-status")).to_have_text("Failed")
                    self.assertEqual(len(read_lines(failed_step_dir / "starts")), 1)

                    page.get_by_role("tab", name="StartupError").click()
                    expect(page.get_by_role("tab", name="StartupError").locator(".sheet-status")).to_have_text("Failed")
                    expect(page.locator("#recovery-hint")).to_be_visible()
                    expect(page.locator("#notice-message")).to_be_hidden()
                    page.get_by_role("button", name="Run flow", exact=True).click()
                    expect(page.locator("#notice-message")).to_contain_text("flow.py")

                    page.get_by_role("tab", name="Manual").click()
                    expect(page.get_by_role("tab", name="Manual").locator(".sheet-status")).to_have_text("Stopped")
                    expect(page.locator("#recovery-hint")).to_be_hidden()
                    expect(page.locator("#notice-message")).to_have_text("Flow stopped.")
                    graph = page.frame_locator('iframe[title="Manual recovery graph"]')
                    graph.get_by_role("button", name="Edit Start source").click()
                    editor = page.get_by_role("dialog", name="Edit source")
                    expect(editor.get_by_label("Python file")).to_have_value("nodes/start/entrypoint.py")
                    editor.get_by_role("button", name="Close", exact=True).click()
                    expect(page.get_by_role("button", name="Run flow", exact=True)).to_be_enabled()
                    expect(page.get_by_role("tab", name="Manual").locator(".sheet-status")).to_have_text("Stopped")
                    self.assertEqual(len(read_lines(stopped_dir / "starts")), 1)

                    (root / "finish").touch()
                    page.get_by_role("tab", name="Main").click()
                    expect(page.get_by_role("tab", name="Main").locator(".sheet-status")).to_have_text("Succeeded")

    def test_deleted_sheet_state_does_not_replay_recreated_sheet_after_restart(self):
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "DeletedSheet"
            state = parent / "state"

            with browser_page() as page:
                with managed_application(state, shutdown_signal=signal.SIGINT) as (url, _):
                    workspace = create_workspace(page, url, state, root)
                    ephemeral = create_sheet(root, "Ephemeral").parent
                    (ephemeral / "nodes/start/entrypoint.py").write_text(
                        '''from pathlib import Path
from time import sleep

def start():
    Path("starts").write_text("first", encoding="utf-8")
    while True:
        sleep(.02)
''',
                        encoding="utf-8",
                    )
                    workspace.click()
                    page.get_by_role("button", name="Run All").click()
                    wait_for(lambda: (ephemeral / "starts").exists())
                    page.get_by_role("button", name="Delete Ephemeral").click()
                    page.get_by_role("dialog", name="Delete sheet?").get_by_role("button", name="Delete sheet").click()
                    expect(page.get_by_role("tab", name="Ephemeral")).to_have_count(0)

                    recreated = create_sheet(root, "Ephemeral").parent
                    (recreated / "nodes/start/entrypoint.py").write_text(
                        '''from pathlib import Path

def start():
    Path("starts").write_text("recreated", encoding="utf-8")
    return "ok"
''',
                        encoding="utf-8",
                    )

            with managed_application(state, shutdown_signal=signal.SIGINT) as (url, _):
                sleep(.6)
                self.assertFalse((root / "flows/Ephemeral/starts").exists())
