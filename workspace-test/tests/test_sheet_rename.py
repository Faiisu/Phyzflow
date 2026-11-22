import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from anyio.from_thread import start_blocking_portal
from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application
from test_application_dashboard import create_workspace_and_token
from test_application_lifecycle import wait_for
from test_application_mcp import mcp_client, output
from workspace_sdk.sheets import create_sheet, find_sheet_by_path


class SheetRenameTests(unittest.TestCase):
    def test_rename_from_ui_and_mcp_preserves_run_and_dashboard_source(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root, state = parent / "workspace", parent / "state"
            with application(state) as url:
                workspace_id, token = create_workspace_and_token(page, url, state, root)
                flow = create_sheet(root, "Telemetry")
                create_sheet(root, "Other")
                cli = [sys.executable, "-c", "from workspace_sdk.cli import main; raise SystemExit(main())",
                       "--project", str(root)]
                self.assertEqual(find_sheet_by_path(root, "."), root)
                self.assertEqual(find_sheet_by_path(root, "flows/Telemetry"), flow.parent)
                outside = parent / "outside"
                outside.mkdir()
                (outside / "flow.py").touch()
                self.assertIsNone(find_sheet_by_path(root, "../outside"))
                self.assertIsNone(find_sheet_by_path(root, outside))
                if os.name != "nt":
                    (root / "flows" / "Outside").symlink_to(outside, target_is_directory=True)
                    self.assertIsNone(find_sheet_by_path(root, "flows/Outside"))
                flow.write_text('''from pathlib import Path
from time import sleep

from workspace_sdk import Flow, Output, Step


def measure():
    for _ in range(400):
        if Path("finish").exists():
            return 42
        sleep(.05)
    raise TimeoutError("The test did not release the flow.")


flow = Flow((), (Step("Measure", measure, {}),), Output("Measure"))
''', encoding="utf-8")
                panel = flow.parent / "dashboards" / "workflow"
                panel.mkdir(parents=True)
                (panel / "index.html").write_text("<h1>Telemetry</h1>", encoding="utf-8")
                (panel / "bridge.json").write_text(json.dumps({"versions": [1]}), encoding="utf-8")
                workspace = f"{url}/workspaces/{workspace_id}"
                placement_res = page.request.post(
                    f"{workspace}/dashboard/placements",
                    headers={"Origin": url},
                    data={"action": "add", "sheet": "flows/Telemetry", "scope": None, "id": "default"},
                )
                self.assertEqual(placement_res.status, 200)
                placement_id = placement_res.json()["placements"][0]["id"]
                page.goto(f"{workspace}/")
                page.get_by_role("tab", name="Telemetry", exact=True).click()
                frame = page.locator('iframe[title="Telemetry flow"]')
                expect(frame).to_be_visible()
                frame_source = frame.get_attribute("src")
                index = int(frame_source.split("/flows/")[1].split("/")[0])
                self.assertEqual(page.request.post(f"{workspace}/flows/{index}/run", headers={"Origin": url}).status, 202)

                def run_status():
                    return page.request.get(f"{workspace}/workspace").json()["flows"][index]

                wait_for(lambda: run_status()["status"] == "running")
                run_id = run_status()["run_id"]
                rename = page.get_by_role("button", name="Rename sheet", exact=True)
                rename.click()
                dialog = page.get_by_role("dialog", name="Rename sheet", exact=True)
                name = dialog.get_by_role("textbox", name="Sheet name")
                expect(name).to_have_value("Telemetry")
                name.fill("other")
                submit = dialog.get_by_role("button", name="Rename sheet", exact=True)
                submit.click()
                expect(dialog.get_by_role("alert")).to_contain_text("already in use")
                name.fill("Live Telemetry")
                submit.click()
                expect(page.get_by_role("tab", name="Live Telemetry", exact=True)).to_have_attribute("aria-selected", "true")
                expect(page.locator('iframe[title="Live Telemetry flow"]')).to_have_attribute("src", frame_source)
                self.assertEqual(run_status()["run_id"], run_id)
                self.assertEqual(run_status()["status"], "running")
                listed = json.loads(subprocess.check_output([*cli, "sheets"], text=True))
                self.assertIn({"name": "Live Telemetry", "file": str(flow)}, listed)
                self.assertNotIn("Outside", {item["name"] for item in listed})

                (flow.parent / "finish").touch()
                wait_for(lambda: run_status()["status"] == "succeeded")
                result = subprocess.run([*cli, "run", "--sheet", "Live Telemetry", "{}"],
                                        capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout), 42)
                snapshot = page.request.get(f"{workspace}/dashboard/placement-assets/{placement_id}/data").json()
                self.assertEqual((snapshot["sheetName"], snapshot["run"]["status"], snapshot["run"]["id"]),
                                 ("Live Telemetry", "succeeded", run_id))

                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    def invoke(tool, **arguments):
                        return portal.call(client.call_tool, tool, {"workspace_id": workspace_id, **arguments})

                    listed = output(invoke("inspect_workspace"))["sheets"]
                    self.assertIn({"name": "Live Telemetry", "entrypoint": "flows/Telemetry/flow.py"}, listed)
                    self.assertTrue(invoke("open_sheet", sheet="Outside", mode="read").is_error)
                    opened = invoke("open_sheet", sheet="Live Telemetry", mode="edit")
                    self.assertFalse(opened.is_error, opened.content)
                    self.assertEqual(output(opened)["entrypoint"], "flows/Telemetry/flow.py")
                    session = output(opened)["session_id"]
                    rename.click()
                    name.fill("Blocked rename")
                    submit.click()
                    expect(dialog.get_by_role("alert")).to_contain_text("locked for editing")
                    dialog.get_by_role("button", name="Cancel", exact=True).click()
                    renamed = invoke("rename_sheet", sheet="Live Telemetry", name="Signals", session_id=session)
                    self.assertEqual(output(renamed), {"name": "Signals", "entrypoint": "flows/Telemetry/flow.py"})
                    self.assertFalse(invoke("close_sheet", sheet="Signals", session_id=session).is_error)
                    history = output(invoke("inspect_execution", sheet="Signals", run_id=run_id))["history"]
                    self.assertEqual(history["id"], run_id)
                    self.assertTrue(invoke("create_sheet", name="signals").is_error)
                    self.assertTrue(invoke("rename_sheet", sheet="Signals", name="Main").is_error)
                    self.assertFalse(invoke("rename_sheet", sheet="Main", name="Primary").is_error)
                    opened = invoke("open_sheet", sheet="Main", mode="read")
                    self.assertEqual(output(opened)["entrypoint"], "flow.py")

                expect(page.get_by_role("tab", name="Signals", exact=True)).to_be_visible()
                page.reload()
                expect(page.get_by_role("tab", name="Primary", exact=True)).to_be_visible()
                expect(page.get_by_role("tab", name="Signals", exact=True)).to_be_visible()
                self.assertEqual(find_sheet_by_path(root, "."), root)
                self.assertEqual(find_sheet_by_path(root, "flows/Telemetry"), flow.parent)
                self.assertIsNone(find_sheet_by_path(root, "flows/Signals"))
                listed = json.loads(subprocess.check_output([*cli, "sheets"], text=True))
                self.assertIn({"name": "Signals", "file": str(flow)}, listed)
                self.assertIn({"name": "Primary", "file": str(root / "flow.py")}, listed)
                result = subprocess.run([*cli, "run", "--sheet", "Main", "{}"],
                                        capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout), {"message": "Hello from Workspace"})
