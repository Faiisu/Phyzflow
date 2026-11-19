from contextlib import contextmanager
import os
from pathlib import Path
import select
from shutil import rmtree
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import sleep
import unittest
from uuid import uuid4

from anyio.from_thread import start_blocking_portal
from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application, sign_in
from test_application_lifecycle import create_workspace, process_exited, wait_for
from test_application_mcp import call, mcp_client, output
from workspace_sdk.sheets import create_sheet


@contextmanager
def application_in_trash(state: Path, trash_dir: Path | None = None):
    cli = Path(sys.executable).with_name("workspace")
    env = dict(os.environ)
    if trash_dir is not None:
        env["PHYZFLOW_TRASH_DIR"] = str(trash_dir)
    else:
        env.pop("PHYZFLOW_TRASH_DIR", None)
    with subprocess.Popen(
        [cli, "app", "--state-dir", state, "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
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


def confirm_workspace_deletion(page, workspace_name):
    page.get_by_role("button", name=f"Delete {workspace_name}").click()
    page.get_by_role("dialog", name="Delete Workspace?").get_by_role("button", name="Move to Trash").click()


def find_supervisor_pid(root: Path) -> int:
    output_text = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
    interpreter = str(root / ".venv" / "bin" / "python")
    for line in output_text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        pid_text, command = parts
        if "agent_supervisor.py" in command and "--worker" not in command and interpreter in command:
            return int(pid_text)
    raise AssertionError("Could not find agent_supervisor process for Workspace.")


def write_observed_child_flow(sheet_dir: Path, root: Path, race_log: Path, *, ignore_sigterm: bool) -> None:
    if ignore_sigterm:
        child_script = f'''
import os
import signal
from pathlib import Path
from time import sleep

signal.signal(signal.SIGTERM, signal.SIG_IGN)
target = {repr(str(root))}
race_log = Path({repr(str(race_log))})
with Path("ticks").open("w", buffering=1) as output:
    while True:
        if not os.path.exists(target):
            with race_log.open("a", encoding="utf-8") as log:
                log.write("moved-while-alive\\n")
            break
        output.write("tick\\n")
        sleep(.01)
'''
    else:
        child_script = f'''
import os
import signal
import sys
from pathlib import Path
from time import monotonic, sleep

stopping = False
def handle_term(*_):
    global stopping
    stopping = True

signal.signal(signal.SIGTERM, handle_term)
target = {repr(str(root))}
race_log = Path({repr(str(race_log))})
with Path("ticks").open("w", buffering=1) as output:
    while not stopping:
        if not os.path.exists(target):
            with race_log.open("a", encoding="utf-8") as log:
                log.write("moved-while-alive\\n")
            break
        output.write("tick\\n")
        sleep(.01)
deadline = monotonic() + .3
while monotonic() < deadline:
    if not os.path.exists(target):
        with race_log.open("a", encoding="utf-8") as log:
            log.write("moved-while-alive\\n")
        break
    sleep(.01)
sys.exit(0)
'''
    (sheet_dir / "nodes/start/entrypoint.py").write_text(
        f'''import os
import subprocess
import sys
from pathlib import Path

def start():
    Path("worker-pid").write_text(str(os.getpid()), encoding="utf-8")
    child = subprocess.Popen([sys.executable, "-c", {repr(child_script)}])
    Path("child-pid").write_text(str(child.pid), encoding="utf-8")
    child.wait()
''',
        encoding="utf-8",
    )


class DeleteWorkspaceTests(unittest.TestCase):
    def test_delete_workspace_stops_sheets_and_children_before_moving_to_trash(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            workspace_name = f"DeleteTarget-{uuid4().hex[:8]}"
            root = parent / workspace_name
            state = parent / "state"
            race_log = parent / "race.log"
            trash_entry = Path.home() / ".Trash" / workspace_name
            try:
                with application(state) as url:
                    workspace = create_workspace(page, url, state, root)
                    secondary = create_sheet(root, "Secondary").parent
                    write_observed_child_flow(root, root, race_log, ignore_sigterm=True)
                    write_observed_child_flow(secondary, root, race_log, ignore_sigterm=False)

                    workspace.click()
                    workspace_url = page.url
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Ready")
                    page.get_by_role("button", name="Run All").click()
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Running")
                    expect(page.get_by_role("tab", name="Secondary")).to_contain_text("Running")
                    for sheet_dir in (root, secondary):
                        ticks = sheet_dir / "ticks"
                        wait_for(lambda t=ticks, d=sheet_dir: t.is_file() and t.stat().st_size > 0 and (d / "child-pid").is_file())

                    pids = [
                        int((sheet_dir / name).read_text(encoding="utf-8"))
                        for sheet_dir in (root, secondary)
                        for name in ("worker-pid", "child-pid")
                    ]
                    for pid in pids:
                        self.assertFalse(process_exited(pid))

                    page.get_by_role("link", name="Workspaces").click()
                    expect(page.get_by_role("heading", name="Workspaces")).to_be_visible()
                    delete = page.get_by_role("button", name=f"Delete {workspace_name}")
                    delete.click()
                    dialog = page.get_by_role("dialog", name="Delete Workspace?")
                    expect(dialog).to_contain_text(str(root))
                    cancel = dialog.get_by_role("button", name="Cancel")
                    expect(cancel).to_be_focused()
                    page.keyboard.press("Escape")
                    expect(dialog).to_be_hidden()
                    expect(delete).to_be_focused()
                    self.assertTrue((root / "flow.py").is_file())
                    for pid in pids:
                        self.assertFalse(process_exited(pid))

                    confirm_workspace_deletion(page, workspace_name)
                    expect(page.get_by_role("link", name=workspace_name)).to_have_count(0, timeout=15_000)
                    expect(page.get_by_role("status")).to_contain_text("No Workspaces yet.")

                    for pid in pids:
                        self.assertTrue(process_exited(pid))
                    self.assertFalse(root.exists())
                    self.assertTrue(trash_entry.is_dir())
                    self.assertTrue((trash_entry / "flow.py").is_file())
                    self.assertFalse(race_log.exists())

                    sleep(.4)
                    ps_output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
                    self.assertNotIn(str(root), ps_output)
                    self.assertNotIn(str(trash_entry), ps_output)

                    page.reload()
                    expect(page.get_by_role("link", name=workspace_name)).to_have_count(0)
                    self.assertEqual(page.request.get(workspace_url).status, 404)
            finally:
                rmtree(trash_entry, ignore_errors=True)

    def test_delete_fails_on_stop_timeout_fences_access_and_retries_after_host_resolution(self):
        with TemporaryDirectory() as temporary, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temporary).resolve()
            workspace_name = f"StuckWorker-{uuid4().hex[:8]}"
            root = parent / workspace_name
            state = parent / "state"
            trash_dir = parent / "trash"
            trash_dir.mkdir()
            supervisor_pid = None
            worker_pids: list[int] = []
            try:
                with application_in_trash(state, trash_dir) as url:
                    workspace = create_workspace(page, url, state, root)
                    workspace_id = page.request.get(f"{url}/workspaces").json()["workspaces"][0]["id"]
                    issued = page.request.post(
                        f"{url}/mcp-tokens",
                        headers={"Origin": url},
                        data={"name": "Delete fence client", "workspace_ids": [workspace_id]},
                    )
                    self.assertEqual(issued.status, 201)
                    token = issued.json()["token"]

                    guest = page.context.browser.new_context()
                    try:
                        self.assertEqual(
                            guest.request.delete(f"{url}/workspaces/{workspace_id}", headers={"Origin": url}).status,
                            401,
                        )
                    finally:
                        guest.close()
                    self.assertEqual(page.request.delete(f"{url}/workspaces/{workspace_id}").status, 403)
                    self.assertEqual(
                        page.request.delete(
                            f"{url}/workspaces/{workspace_id}",
                            headers={"Origin": "https://untrusted.example"},
                        ).status,
                        403,
                    )

                    secondary = create_sheet(root, "Secondary").parent
                    for sheet_dir in (root, secondary):
                        (sheet_dir / "nodes/start/entrypoint.py").write_text(
                            "import os\nfrom pathlib import Path\nfrom time import sleep\n"
                            "def start():\n"
                            "    Path('worker-pid').write_text(str(os.getpid()), encoding='utf-8')\n"
                            "    with Path('ticks').open('w', buffering=1) as output:\n"
                            "        while True:\n"
                            "            output.write('tick\\n')\n"
                            "            sleep(.03)\n",
                            encoding="utf-8",
                        )

                    workspace.click()
                    workspace_url = page.url
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Ready")
                    page.get_by_role("button", name="Run All").click()
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Running")
                    expect(page.get_by_role("tab", name="Secondary")).to_contain_text("Running")
                    for sheet_dir in (root, secondary):
                        wait_for(lambda d=sheet_dir: (d / "worker-pid").is_file() and (d / "ticks").is_file())
                        worker_pids.append(int((sheet_dir / "worker-pid").read_text(encoding="utf-8")))
                    supervisor_pid = find_supervisor_pid(root)

                    page.get_by_role("link", name="Workspaces").click()
                    expect(page.get_by_role("heading", name="Workspaces")).to_be_visible()
                    os.kill(supervisor_pid, signal.SIGSTOP)

                    with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                        self.assertEqual(output(call(portal, client, "list_workspaces")), {
                            "workspaces": [{"id": workspace_id, "name": workspace_name}],
                        })
                        confirm_workspace_deletion(page, workspace_name)
                        sleep(.5)
                        fenced_http = page.request.get(f"{workspace_url}workspace")
                        self.assertEqual(fenced_http.status, 409)
                        fenced_mcp = call(portal, client, "inspect_workspace", workspace_id=workspace_id)
                        self.assertTrue(fenced_mcp.is_error)
                        self.assertIn("being deleted", fenced_mcp.content[0].text)

                    alert = page.get_by_role("alert")
                    expect(alert).to_be_visible(timeout=20_000)
                    expect(alert).to_contain_text("still running")
                    expect(alert).to_contain_text(str(supervisor_pid))
                    expect(alert).to_contain_text("operating system")
                    expect(alert).to_contain_text("retrying Delete")
                    expect(page.get_by_role("link", name=workspace_name)).to_be_visible()
                    self.assertTrue((root / "flow.py").is_file())
                    self.assertFalse((trash_dir / workspace_name).exists())

                    secondary_pid = worker_pids[1]
                    wait_for(lambda: process_exited(secondary_pid))
                    secondary_size = (secondary / "ticks").stat().st_size
                    sleep(.2)
                    self.assertEqual((secondary / "ticks").stat().st_size, secondary_size)

                    os.kill(supervisor_pid, signal.SIGCONT)
                    wait_for(lambda: process_exited(supervisor_pid))
                    supervisor_pid = None

                    confirm_workspace_deletion(page, workspace_name)
                    expect(page.get_by_role("link", name=workspace_name)).to_have_count(0, timeout=10_000)
                    expect(alert).to_be_hidden()
                    self.assertFalse(root.exists())
                    self.assertTrue((trash_dir / workspace_name / "flow.py").is_file())
                    for pid in worker_pids:
                        self.assertTrue(process_exited(pid))
            finally:
                if supervisor_pid is not None:
                    try:
                        os.kill(supervisor_pid, signal.SIGCONT)
                        os.kill(supervisor_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                for pid in worker_pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_delete_detects_detached_workspace_process_even_when_unmounted_and_retries(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            workspace_name = f"Detached-{uuid4().hex[:8]}"
            root = parent / workspace_name
            state = parent / "state"
            trash_dir = parent / "trash"
            trash_dir.mkdir()
            detached_pid = None
            try:
                with application_in_trash(state, trash_dir) as url:
                    workspace = create_workspace(page, url, state, root)
                    (root / "nodes/start/entrypoint.py").write_text(
                        "import subprocess, sys\n"
                        "from pathlib import Path\n"
                        "from time import sleep\n"
                        "def start():\n"
                        "    child = subprocess.Popen(\n"
                        "        [sys.executable, '-c', 'from time import sleep; sleep(60)'],\n"
                        "        start_new_session=True,\n"
                        "    )\n"
                        "    Path('detached-pid').write_text(str(child.pid), encoding='utf-8')\n"
                        "    while True:\n"
                        "        sleep(.05)\n",
                        encoding="utf-8",
                    )
                    workspace.click()
                    page.get_by_role("button", name="Run flow").click()
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Running")
                    wait_for(lambda: (root / "detached-pid").is_file())
                    detached_pid = int((root / "detached-pid").read_text(encoding="utf-8"))
                    self.assertFalse(process_exited(detached_pid))

                    page.get_by_role("link", name="Workspaces").click()
                    confirm_workspace_deletion(page, workspace_name)
                    alert = page.get_by_role("alert")
                    expect(alert).to_be_visible(timeout=10_000)
                    expect(alert).to_contain_text("still running")
                    expect(alert).to_contain_text(str(detached_pid))
                    expect(alert).to_contain_text("operating system")
                    expect(page.get_by_role("link", name=workspace_name)).to_be_visible()
                    self.assertTrue((root / "flow.py").is_file())

                with application_in_trash(state, trash_dir) as url:
                    sign_in(page, url, state)
                    confirm_workspace_deletion(page, workspace_name)
                    alert = page.get_by_role("alert")
                    expect(alert).to_be_visible(timeout=10_000)
                    expect(alert).to_contain_text(str(detached_pid))
                    expect(page.get_by_role("link", name=workspace_name)).to_be_visible()
                    self.assertTrue((root / "flow.py").is_file())

                    os.kill(detached_pid, signal.SIGKILL)
                    wait_for(lambda: process_exited(detached_pid))
                    detached_pid = None

                    confirm_workspace_deletion(page, workspace_name)
                    expect(page.get_by_role("link", name=workspace_name)).to_have_count(0, timeout=10_000)
                    self.assertFalse(root.exists())
                    self.assertTrue((trash_dir / workspace_name / "flow.py").is_file())
            finally:
                if detached_pid is not None:
                    try:
                        os.kill(detached_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_delete_keeps_workspace_registered_when_trash_move_is_rejected(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            workspace_name = f"TrashRejected-{uuid4().hex[:8]}"
            root = parent / workspace_name
            state = parent / "state"
            trash_dir = parent / "readonly-trash"
            trash_dir.mkdir()
            try:
                with application_in_trash(state, trash_dir) as url:
                    create_workspace(page, url, state, root)
                    trash_dir.chmod(0o500)
                    confirm_workspace_deletion(page, workspace_name)

                    alert = page.get_by_role("alert")
                    expect(alert).to_be_visible(timeout=10_000)
                    expect(alert).to_contain_text(f"Could not move {workspace_name} to Trash")
                    expect(page.get_by_role("link", name=workspace_name)).to_be_visible()
                    self.assertTrue((root / "flow.py").is_file())
                    self.assertFalse((trash_dir / workspace_name).exists())

                    page.reload()
                    expect(page.get_by_role("link", name=workspace_name)).to_be_visible()

                    trash_dir.chmod(0o700)
                    confirm_workspace_deletion(page, workspace_name)
                    expect(page.get_by_role("link", name=workspace_name)).to_have_count(0, timeout=10_000)
                    self.assertFalse(root.exists())
                    self.assertTrue((trash_dir / workspace_name / "flow.py").is_file())

                    create_workspace(page, url, state, root)
                    confirm_workspace_deletion(page, workspace_name)
                    expect(page.get_by_role("link", name=workspace_name)).to_have_count(0, timeout=10_000)
                    self.assertFalse(root.exists())
                    self.assertTrue((trash_dir / f"{workspace_name} 2" / "flow.py").is_file())
            finally:
                trash_dir.chmod(0o700)
