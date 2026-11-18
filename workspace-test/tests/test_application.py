from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import select
import signal
import subprocess
import sys
import unittest

from playwright.sync_api import expect

from browser_support import browser_page


@contextmanager
def application(state: Path):
    cli = Path(sys.executable).with_name("workspace")
    with subprocess.Popen(
        [cli, "app", "--state-dir", state, "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        try:
            if not select.select([process.stdout], [], [], 10)[0]:
                raise RuntimeError("The Phyzflow application did not start within ten seconds.")
            prefix = "Phyzflow: "
            line = process.stdout.readline().strip()
            if not line.startswith(prefix):
                raise RuntimeError(f"Phyzflow did not publish its address: {line!r}.")
            yield line.removeprefix(prefix)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


class ApplicationTests(unittest.TestCase):
    def test_create_and_reopen_a_workspace_after_application_restart(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "Diagnostics"
            state = parent / "state"
            with application(state) as url:
                page.goto(url)
                page.get_by_role("button", name="Create Workspace").click()
                page.get_by_label("New folder path").fill(str(root))
                page.get_by_role("button", name="Create", exact=True).click()
                workspace = page.get_by_role("link", name="Diagnostics")
                expect(workspace).to_be_visible(timeout=20_000)
                interpreter = root / ".venv/bin/python"
                actual = subprocess.check_output(
                    [interpreter, "-c", "import json, sys; print(json.dumps(sys.version_info[:2]))"],
                    text=True,
                )
                self.assertEqual(json.loads(actual), [3, 12])
                workspace.click()
                expect(page.get_by_role("tab", name="Main")).to_be_visible()

            with application(state) as url:
                page.goto(url)
                page.get_by_role("link", name="Diagnostics").click()
                expect(page.get_by_role("tab", name="Main")).to_be_visible()

    def test_run_a_sample_in_the_workspace_interpreter_and_show_both_console_streams(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "Diagnostics"
            state = parent / "state"
            with application(state) as url:
                page.goto(url)
                page.get_by_role("button", name="Create Workspace").click()
                page.get_by_label("New folder path").fill(str(root))
                page.get_by_role("button", name="Create", exact=True).click()
                workspace = page.get_by_role("link", name="Diagnostics")
                expect(workspace).to_be_visible(timeout=20_000)
                interpreter = root / ".venv/bin/python"
                (root / "nodes/start/entrypoint.py").write_text(
                    r'''from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep

def start():
    print("Preparing the sample")
    subprocess.run([sys.executable, "-c", 'import os; os.write(2, b"Child diagnostics\\n")'], check=True)
    deadline = monotonic() + 15
    while not Path("finish-now").exists() and monotonic() < deadline:
        sleep(.02)
    return sys.executable

def finish(interpreter):
    return {"message": "Finished", "interpreter": interpreter}
''',
                    encoding="utf-8",
                )
                (root / "flow.py").write_text(
                    "from workspace_sdk import Flow, Output, Step\n"
                    "from nodes.start.entrypoint import finish, start\n"
                    'flow = Flow((), (Step("Prepare", start, {}), '
                    'Step("Finish", finish, {"interpreter": Output("Prepare")})), Output("Finish"))\n',
                    encoding="utf-8",
                )

                workspace.click()
                flow = page.frame_locator('iframe[title="Main flow"]')
                expect(flow.get_by_role("button", name="Inspect Prepare")).to_be_visible()
                expect(flow.get_by_role("button", name="Inspect Finish")).to_be_visible()
                flow.get_by_role("button", name="Run flow").click()
                console = flow.get_by_role("region", name="Run console")
                expect(console.get_by_label("stdout")).to_contain_text("Preparing the sample")
                expect(console.get_by_label("stderr")).to_contain_text("Child diagnostics")
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Running")
                (root / "finish-now").touch()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                self.assertEqual(
                    json.loads(flow.locator("#result pre").inner_text()),
                    {"message": "Finished", "interpreter": str(interpreter)},
                )

            with application(state) as url:
                page.goto(url)
                page.get_by_role("link", name="Diagnostics").click()
                flow = page.frame_locator('iframe[title="Main flow"]')
                flow.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                self.assertEqual(
                    json.loads(flow.locator("#result pre").inner_text()),
                    {"message": "Finished", "interpreter": str(interpreter)},
                )
