import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from anyio.from_thread import start_blocking_portal
from browser_support import browser_page
from playwright.sync_api import expect
from test_application import application
from test_application_dashboard import create_workspace_and_token
from test_application_mcp import call, mcp_client, output


class ApplicationDashboardFilesTests(unittest.TestCase):
    def test_workflow_files_use_panel_scope_and_workspace_authentication(self):
        with TemporaryDirectory() as folder, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(folder).resolve()
            root, state = parent / "workspace", parent / "state"
            with application(state) as url:
                workspace_id, token = create_workspace_and_token(page, url, state, root)
                workspace_url = f"{url}/workspaces/{workspace_id}/"
                source = root / "dashboards/workflow"
                source.mkdir(parents=True, exist_ok=True)
                (source / "index.html").write_text(
                    '<output aria-label="Report"></output><audio controls></audio>'
                    '<button disabled>Play recording</button><output aria-label="Playback"></output>',
                    encoding="utf-8",
                )
                (source / "bridge.json").write_text('{"versions":[1],"file_root":"reports"}', encoding="utf-8")
                (source / "dashboard.js").write_text(
                    'window.workspaceDashboard.readFile("recording.json").then(file => { '
                    'document.querySelector("output").textContent = JSON.parse(new TextDecoder().decode(file)).message; '
                    'return window.workspaceDashboard.readFile("recording.wav"); '
                    '}).then(file => { '
                    'const audio = document.querySelector("audio"); '
                    'const button = document.querySelector("button"); '
                    'audio.src = URL.createObjectURL(new Blob([file], {type: "audio/wav"})); '
                    'audio.addEventListener("canplay", () => { button.disabled = false; }, {once: true}); '
                    'button.addEventListener("click", async () => { '
                    'await audio.play(); document.querySelector(\'[aria-label="Playback"]\').textContent = "Playing"; '
                    '}); '
                    '});',
                    encoding="utf-8",
                )
                (root / "reports").mkdir()
                report = '{"message":"Saved recording ready"}'
                (root / "reports/recording.json").write_text(report, encoding="utf-8")
                with wave.open(str(root / "reports/recording.wav"), "wb") as stream:
                    stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                    stream.writeframes(bytes(16000))
                (root / "outside.json").write_text("{}", encoding="utf-8")
                (root / "reports/settings.py").write_text("secret = None", encoding="utf-8")
                (root / "reports/.secret.json").write_text("{}", encoding="utf-8")
                (root / "reports/alias.json").hardlink_to(root / "outside.json")
                with (root / "reports/oversized.wav").open("wb") as stream:
                    stream.truncate(16 * 1024 * 1024 + 1)

                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    inspected = output(call(portal, client, "inspect_dashboards", workspace_id=workspace_id))
                    self.assertIn("dashboards/workflow/bridge.json", inspected["workflow"][0]["assets"])
                    placement = output(call(portal, client, "add_dashboard_placement", workspace_id=workspace_id))
                    read = output(call(portal, client, "read_workspace_text",
                                       workspace_id=workspace_id, path="reports/recording.json"))
                    self.assertEqual(read["text"], report)
                placement_id = placement["placements"][0]["id"]
                placement_file = f"dashboard/placement-assets/{placement_id}/file"
                flow_file = "flows/0/view/dashboard-files/default"

                for route in (placement_file, flow_file):
                    response = page.request.get(f"{workspace_url}{route}?path=recording.json")
                    self.assertEqual(response.status, 200, response.text())
                    self.assertEqual(response.headers["content-type"], "application/octet-stream")
                    self.assertEqual(response.text(), report)
                    self.assertEqual(page.request.get(f"{workspace_url}{route}?path=oversized.wav").status, 413)
                    for invalid in ("../outside.json", "settings.py", ".secret.json", "alias.json", "missing.wav"):
                        query = urlencode({"path": invalid})
                        self.assertEqual(page.request.get(f"{workspace_url}{route}?{query}").status, 404)

                guest = page.context.browser.new_context()
                try:
                    denied = guest.request.get(f"{workspace_url}{placement_file}?path=recording.json")
                    self.assertEqual(denied.status, 401)
                finally:
                    guest.close()

                page.goto(workspace_url)
                page.get_by_role("button", name="Dashboard", exact=True).click()
                board = page.get_by_role("dialog", name="Workspace dashboard").frame_locator(
                    'iframe[title="Workspace Dashboard"]'
                )
                panel = board.get_by_role("region", name="Main · Workflow · Overview")
                frame = panel.frame_locator("iframe")
                expect(frame.get_by_label("Report")).to_have_text("Saved recording ready")
                frame.get_by_role("button", name="Play recording").click(timeout=5000)
                expect(frame.get_by_label("Playback")).to_have_text("Playing")
