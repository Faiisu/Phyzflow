from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import select
import signal
from stat import S_IMODE
import subprocess
import sys
import unittest

from playwright.sync_api import expect

from browser_support import browser_page


@contextmanager
def application(state: Path, *, args: tuple[str, ...] = ("--port", "0")):
    cli = Path(sys.executable).with_name("workspace")
    with subprocess.Popen(
        [cli, "app", "--state-dir", state, *args],
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


def sign_in(page, url: str, state: Path) -> None:
    page.goto(f"{url}/login")
    page.get_by_label("Password").fill((state / "admin" / "initial-password").read_text().strip())
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", name="Workspaces")).to_be_visible()


class ApplicationTests(unittest.TestCase):
    def test_admin_saves_network_settings_for_the_next_server_start(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            state = Path(temp) / "state"
            with application(state) as url:
                endpoint = f"{url}/network-settings"
                port = int(url.rsplit(":", 1)[1])
                network = {"ipv4": "0.0.0.0", "port": port}
                self.assertEqual(page.request.get(endpoint).status, 401)
                self.assertEqual(page.request.post(endpoint, data=network, headers={"Origin": url}).status, 401)
                page.goto(f"{url}/settings/network")
                expect(page.get_by_role("heading", name="Sign in to Phyzflow")).to_be_visible()
                sign_in(page, url, state)
                self.assertEqual(page.request.post(endpoint, data=network, headers={"Origin": "http://example.invalid"}).status, 403)
                invalid_port = page.request.post(endpoint, data={**network, "port": 65536}, headers={"Origin": url})
                self.assertEqual(invalid_port.status, 400)
                self.assertIn("The server port", invalid_port.json()["error"])

                page.get_by_role("link", name="Network settings").click()
                expect(page.get_by_role("heading", name="Network settings")).to_be_visible()
                address = page.get_by_label("Bind IPv4 address")
                expect(address).to_have_value("127.0.0.1")
                expect(page.get_by_label("Port")).to_have_value("18080")
                expect(page.locator("#active-listener")).to_have_text(f"127.0.0.1:{port}")
                page.get_by_label("Port").fill(str(port))
                address.fill("300.0.0.1")
                save = page.get_by_role("button", name="Save settings")
                save.click()
                expect(page.get_by_role("alert")).to_contain_text("must be an IPv4 address")
                self.assertFalse((state / "network.json").exists())

                address.fill(network["ipv4"])
                save.click()
                expect(page.get_by_role("status")).to_contain_text("Restart Phyzflow")
                expect(page.locator("#active-listener")).to_have_text(f"127.0.0.1:{port}")
                page.reload()
                expect(address).to_have_value(network["ipv4"])
                expect(page.get_by_label("Port")).to_have_value(str(port))
                page.get_by_role("link", name="Workspaces").click()
                expect(page.get_by_role("heading", name="Workspaces")).to_be_visible()

            with application(state, args=()) as restarted:
                self.assertEqual(restarted, url)
                sign_in(page, restarted, state)
                self.assertEqual(page.request.get(f"{restarted}/network-settings").json(), {"saved": network, "active": network})

            with application(state, args=("--ipv4", "127.0.0.1", "--port", "0")) as overridden:
                sign_in(page, overridden, state)
                settings = page.request.get(f"{overridden}/network-settings").json()
                self.assertEqual(settings["saved"], network)
                self.assertEqual(settings["active"]["ipv4"], "127.0.0.1")
                self.assertEqual(settings["active"]["port"], int(overridden.rsplit(":", 1)[1]))

    def test_sign_in_with_the_installation_administrator(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            state = Path(temp).resolve() / "state"
            with application(state) as url:
                page.goto(url)
                expect(page.get_by_role("heading", name="Sign in to Phyzflow")).to_be_visible()
                expect(page.get_by_label("Password")).not_to_be_focused()
                page.get_by_label("Username").fill("admin")
                page.get_by_label("Password").fill("incorrect")
                page.get_by_role("button", name="Sign in").click()
                expect(page.get_by_role("alert")).to_contain_text("Incorrect username or password.")
                expect(page.get_by_role("alert")).to_be_focused()
                self.assertFalse(bool(page.context.cookies(url)))

                page.get_by_label("Username").fill("admin")
                password = (state / "admin" / "initial-password").read_text().strip()
                page.get_by_label("Password").fill(password)
                page.get_by_role("button", name="Sign in").click()
                expect(page.get_by_role("heading", name="Workspaces")).to_be_visible()
                session, = page.context.cookies(url)
                self.assertTrue(session["httpOnly"])
                self.assertEqual(session["sameSite"], "Strict")
                self.assertFalse(password in session["value"])

    def test_independent_installations_keep_a_private_administrator_credential_after_restart(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            first, second = parent / "first", parent / "second"
            with application(first) as first_url, application(second) as second_url:
                first_delivery = first / "admin" / "initial-password"
                second_delivery = second / "admin" / "initial-password"
                for delivery in (first_delivery, second_delivery):
                    self.assertEqual(S_IMODE(delivery.parent.stat().st_mode), 0o700)
                    self.assertEqual(S_IMODE(delivery.stat().st_mode), 0o600)
                first_password = first_delivery.read_text().strip()
                self.assertTrue(first_password != second_delivery.read_text().strip(), "Each installation needs its own password.")
                page.goto(second_url)
                page.get_by_label("Password").fill(first_password)
                page.get_by_role("button", name="Sign in").click()
                expect(page.get_by_role("alert")).to_be_visible()
                page.get_by_label("Username").fill("other")
                page.get_by_label("Password").fill(second_delivery.read_text().strip())
                page.get_by_role("button", name="Sign in").click()
                expect(page.get_by_role("alert")).to_be_visible()
                sign_in(page, second_url, second)
                sign_in(page, first_url, first)
                self.assertEqual(page.request.get(f"{second_url}/workspaces").status, 200)
                first_delivery.unlink()

            with application(first) as url:
                self.assertFalse(first_delivery.exists())
                self.assertEqual(page.request.get(f"{url}/workspaces").status, 401)
                page.goto(url)
                page.get_by_label("Password").fill(first_password)
                page.get_by_role("button", name="Sign in").click()
                expect(page.get_by_role("heading", name="Workspaces")).to_be_visible()

    def test_app_requires_a_session_for_workspace_and_flow_routes_and_same_origin_for_writes(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            state = parent / "state"
            root = parent / "Diagnostics"
            with application(state) as url:
                sign_in(page, url, state)
                page.get_by_role("button", name="Create Workspace").click()
                page.get_by_role("button", name="Enter a folder path").click()
                page.get_by_label("New folder path").fill(str(root))
                page.get_by_role("button", name="Create", exact=True).click()
                page.get_by_role("link", name="Diagnostics").click(timeout=20_000)
                frame = page.locator('iframe[title="Main flow"]')
                expect(frame).to_be_visible()
                workspace = page.url.rstrip("/")
                view = frame.content_frame.locator("html").evaluate("document => document.baseURI").rstrip("/")
                self.assertEqual(page.request.get(f"{workspace}/flows/0/source").status, 200)
                self.assertEqual(page.request.get(f"{view}/history").status, 200)
                guest = page.context.browser.new_context()
                try:
                    for target in (
                        f"{url}/workspaces", f"{workspace}/", f"{workspace}/workspace",
                        f"{workspace}/flows/0/source", f"{view}/", f"{view}/history", f"{view}/live-run",
                    ):
                        with self.subTest(method="GET", target=target):
                            self.assertEqual(guest.request.get(target).status, 401)
                    for target in (f"{url}/workspaces", f"{workspace}/flows/0/source", f"{view}/run"):
                        with self.subTest(method="POST", target=target):
                            self.assertEqual(guest.request.post(target, headers={"Origin": url}).status, 401)
                finally:
                    guest.close()

                for headers in ({}, {"Origin": "https://untrusted.example"}):
                    self.assertEqual(page.request.post(f"{view}/run", headers=headers).status, 403)
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Ready")

    def test_create_and_reopen_a_workspace_after_application_restart(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "Diagnostics"
            state = parent / "state"
            with application(state) as url:
                sign_in(page, url, state)
                page.get_by_role("button", name="Create Workspace").click()
                page.get_by_role("button", name="Enter a folder path").click()
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
                sign_in(page, url, state)
                page.get_by_role("link", name="Diagnostics").click()
                expect(page.get_by_role("tab", name="Main")).to_be_visible()

    def test_run_a_sample_in_the_workspace_interpreter_and_show_both_console_streams(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "Diagnostics"
            state = parent / "state"
            with application(state) as url:
                sign_in(page, url, state)
                page.get_by_role("button", name="Create Workspace").click()
                page.get_by_role("button", name="Enter a folder path").click()
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
                page.get_by_role("button", name="Run flow").click()
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
                sign_in(page, url, state)
                page.get_by_role("link", name="Diagnostics").click()
                flow = page.frame_locator('iframe[title="Main flow"]')
                page.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                self.assertEqual(
                    json.loads(flow.locator("#result pre").inner_text()),
                    {"message": "Finished", "interpreter": str(interpreter)},
                )
