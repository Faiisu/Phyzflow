import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import unittest

import anyio
from anyio.from_thread import start_blocking_portal
from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application, sign_in
from test_application_mcp import call, mcp_client, output


def setup_workspace_with_two_sheets(page, url, state, root):
    sign_in(page, url, state)
    create = page.request.post(
        f"{url}/workspaces", headers={"Origin": url}, data={"path": str(root)},
    )
    if create.status != 201:
        raise AssertionError(f"Could not create workspace: {create.status} {create.text()}")
    workspace_id = create.json()["id"]
    second = page.request.post(
        f"{url}/workspaces/{workspace_id}/flows",
        headers={"Origin": url},
        data={"name": "Second"},
    )
    if second.status != 201:
        raise AssertionError(f"Could not create second sheet: {second.status} {second.text()}")
    return workspace_id


def issue_token(page, url, name, workspace_ids):
    response = page.request.post(
        f"{url}/mcp-tokens",
        headers={"Origin": url},
        data={"name": name, "workspace_ids": workspace_ids},
    )
    if response.status != 201:
        raise AssertionError(f"Could not issue token: {response.status} {response.text()}")
    return response.json()["token"]


def wait_for_open_sheet(portal, client, workspace_id, sheet="Main"):
    deadline = monotonic() + 5
    last = None
    while monotonic() < deadline:
        last = call(portal, client, "open_sheet", workspace_id=workspace_id, sheet=sheet, mode="edit")
        if not last.is_error:
            return last
        sleep(0.05)
    return last


def wait_for(check, timeout=8):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if check():
            return
        sleep(0.02)
    raise AssertionError("Timed out waiting for expected state.")


def process_exited(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


class ApplicationEditLocksTests(unittest.TestCase):
    def test_mcp_exclusive_sheet_editing_allows_read_only_and_other_sheets(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "Locks"
            state = parent / "state"
            with application(state) as url:
                workspace_id = setup_workspace_with_two_sheets(page, url, state, root)
                token_a = issue_token(page, url, "Client A", [workspace_id])
                token_b = issue_token(page, url, "Client B", [workspace_id])

                main_flow = root / "flow.py"
                second_flow = root / "flows" / "Second" / "flow.py"
                original_main = main_flow.read_text(encoding="utf-8")

                with (
                    portal.wrap_async_context_manager(mcp_client(url, token_a)) as client_a,
                    portal.wrap_async_context_manager(mcp_client(url, token_b)) as client_b,
                ):
                    opened_a = call(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(opened_a.is_error, opened_a.content[0].text)
                    data_a = output(opened_a)
                    session_a = data_a["session_id"]
                    self.assertTrue(session_a)
                    self.assertTrue(data_a["edit_lock"]["locked"])
                    self.assertTrue(data_a["edit_lock"]["owned_by_viewer"])
                    self.assertIn("Client A", data_a["edit_lock"]["owner"])

                    beat = call(portal, client_a, "heartbeat_sheet", workspace_id=workspace_id, sheet="Main", session_id=session_a)
                    self.assertFalse(beat.is_error, beat.content[0].text)
                    self.assertTrue(output(beat)["locked"])

                    denied_open = call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertTrue(denied_open.is_error)
                    self.assertIn("Client A", denied_open.content[0].text)

                    readonly = call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="read")
                    self.assertFalse(readonly.is_error, readonly.content[0].text)
                    readonly_data = output(readonly)
                    self.assertTrue(readonly_data["read_only"])
                    self.assertTrue(readonly_data["edit_lock"]["locked"])
                    self.assertFalse(readonly_data["edit_lock"]["owned_by_viewer"])
                    self.assertIsNone(readonly_data["edit_lock"]["session_id"])

                    read_main = call(portal, client_b, "read_file", workspace_id=workspace_id, path="flow.py", sheet="Main")
                    self.assertFalse(read_main.is_error, read_main.content[0].text)
                    main_rev = output(read_main)["revision"]
                    self.assertEqual(output(read_main)["content"], original_main)

                    denied_write = call(
                        portal, client_b, "write_file",
                        workspace_id=workspace_id, path="flow.py", revision=main_rev,
                        source=original_main + "\n", sheet="Main",
                    )
                    self.assertTrue(denied_write.is_error)
                    self.assertIn("Client A", denied_write.content[0].text)

                    denied_patch = call(
                        portal, client_b, "apply_patch",
                        workspace_id=workspace_id, path="flow.py", revision=main_rev,
                        old_text='"Start"', new_text='"Hijacked"', sheet="Main",
                    )
                    self.assertTrue(denied_patch.is_error)
                    self.assertIn("Client A", denied_patch.content[0].text)
                    self.assertEqual(main_flow.read_text(encoding="utf-8"), original_main)

                    denied_close = call(
                        portal, client_b, "close_sheet",
                        workspace_id=workspace_id, sheet="Main", session_id=session_a,
                    )
                    self.assertTrue(denied_close.is_error)
                    denied_beat = call(
                        portal, client_b, "heartbeat_sheet",
                        workspace_id=workspace_id, sheet="Main", session_id=session_a,
                    )
                    self.assertTrue(denied_beat.is_error)

                    opened_b = call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Second", mode="edit")
                    self.assertFalse(opened_b.is_error, opened_b.content[0].text)
                    session_b = output(opened_b)["session_id"]
                    read_second = output(call(portal, client_b, "read_file", workspace_id=workspace_id, path="flow.py", sheet="Second"))
                    patched_second = call(
                        portal, client_b, "apply_patch",
                        workspace_id=workspace_id, path="flow.py", revision=read_second["revision"],
                        old_text='steps=(Step("Start", start, {}),),',
                        new_text='steps=(Step("Start", start, {}),),\n',
                        sheet="Second", session_id=session_b,
                    )
                    self.assertFalse(patched_second.is_error, patched_second.content[0].text)
                    self.assertIn('steps=(Step("Start", start, {}),),\n', second_flow.read_text(encoding="utf-8"))
                    closed_b = call(portal, client_b, "close_sheet", workspace_id=workspace_id, sheet="Second", session_id=session_b)
                    self.assertFalse(closed_b.is_error, closed_b.content[0].text)

                    read_entry = output(call(
                        portal, client_a, "read_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py", sheet="Main",
                    ))
                    updated_entry = read_entry["content"].replace("Hello from Workspace", "Edited by A")
                    written_a = call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py",
                        revision=read_entry["revision"], source=updated_entry,
                        sheet="Main", session_id=session_a,
                    )
                    self.assertFalse(written_a.is_error, written_a.content[0].text)
                    self.assertIn("Edited by A", (root / "nodes/start/entrypoint.py").read_text(encoding="utf-8"))

                    closed_a = call(portal, client_a, "close_sheet", workspace_id=workspace_id, sheet="Main", session_id=session_a)
                    self.assertFalse(closed_a.is_error, closed_a.content[0].text)
                    self.assertTrue(output(closed_a)["released"])

                    stale_beat = call(portal, client_a, "heartbeat_sheet", workspace_id=workspace_id, sheet="Main", session_id=session_a)
                    self.assertTrue(stale_beat.is_error)
                    stale_write = call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py",
                        revision=output(written_a)["revision"], source=updated_entry + "\n",
                        sheet="Main", session_id=session_a,
                    )
                    self.assertTrue(stale_write.is_error)

                    reopened_b = call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(reopened_b.is_error, reopened_b.content[0].text)
                    session_b_main = output(reopened_b)["session_id"]
                    patched_b = call(
                        portal, client_b, "apply_patch",
                        workspace_id=workspace_id, path="flow.py", revision=main_rev,
                        old_text='steps=(Step("Start", start, {}),),\n    output=Output("Start"),',
                        new_text='steps=(Step("Edited by B", start, {}),),\n    output=Output("Edited by B"),',
                        sheet="Main", session_id=session_b_main,
                    )
                    self.assertFalse(patched_b.is_error, patched_b.content[0].text)
                    self.assertIn('"Edited by B"', main_flow.read_text(encoding="utf-8"))
                    closed_b_main = call(
                        portal, client_b, "close_sheet",
                        workspace_id=workspace_id, sheet="Main", session_id=session_b_main,
                    )
                    self.assertFalse(closed_b_main.is_error, closed_b_main.content[0].text)

    def test_web_browser_follows_mcp_lock_read_only_and_keeps_unlocked_sheet_editable(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "WebReadOnly"
            state = parent / "state"
            with application(state) as url:
                workspace_id = setup_workspace_with_two_sheets(page, url, state, root)
                token_a = issue_token(page, url, "Client A", [workspace_id])
                main_flow = root / "flow.py"
                second_flow = root / "flows" / "Second" / "flow.py"
                original_main = main_flow.read_text(encoding="utf-8")

                with portal.wrap_async_context_manager(mcp_client(url, token_a)) as client_a:
                    opened = call(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(opened.is_error, opened.content[0].text)
                    session_a = output(opened)["session_id"]
                    main_rev = output(call(portal, client_a, "read_file", workspace_id=workspace_id, path="flow.py", sheet="Main"))["revision"]
                    entry_rev = output(call(
                        portal, client_a, "read_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py", sheet="Main",
                    ))["revision"]

                    page.goto(f"{url}/workspaces/{workspace_id}/")
                    flow = page.frame_locator('iframe[title="Main flow"]')
                    expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()
                    expect(page.locator("#edit-lock-message")).to_be_visible()
                    expect(page.locator("#edit-lock-message")).to_contain_text("Read-only")
                    expect(page.locator("#edit-lock-message")).to_contain_text("Client A")

                    page.get_by_role("button", name="Edit flow.py").click()
                    flow_dialog = page.get_by_role("dialog", name="Edit flow.py")
                    flow_editor = flow_dialog.get_by_role("textbox", name="Source code")
                    expect(flow_editor).to_have_value(original_main)
                    expect(flow_editor).not_to_be_editable()
                    expect(flow_dialog.get_by_role("button", name="Save", exact=True)).to_be_disabled()
                    expect(flow_dialog.get_by_role("status")).to_contain_text("Client A")
                    flow_dialog.get_by_role("button", name="Close").click()

                    flow.get_by_role("button", name="Inspect Start").click()
                    flow.get_by_role("button", name="View files").click()
                    node_dialog = flow.get_by_role("dialog", name="Start files")
                    node_editor = node_dialog.get_by_role("textbox", name="Source code")
                    expect(node_editor).to_have_value(re.compile("Hello from Workspace"))
                    expect(node_editor).not_to_be_editable()
                    expect(node_dialog.get_by_role("button", name="Save", exact=True)).to_be_disabled()
                    expect(node_dialog.locator("#node-source-message")).to_contain_text("Client A")
                    node_dialog.get_by_role("button", name="Close node files").click()

                    direct_flow = page.request.post(
                        f"{url}/workspaces/{workspace_id}/flows/0/source",
                        headers={"Origin": url},
                        data={"source": original_main + "\n", "revision": main_rev},
                    )
                    self.assertEqual(direct_flow.status, 409)
                    self.assertIn("Client A", direct_flow.json()["error"])

                    frame_src = page.locator('iframe[title="Main flow"]').get_attribute("src")
                    direct_node = page.request.post(
                        f"{url}{frame_src}node-source/0",
                        headers={"Origin": url},
                        data={"path": "nodes/start/entrypoint.py", "source": "def start():\n    return {}\n", "revision": entry_rev},
                    )
                    self.assertEqual(direct_node.status, 409)
                    self.assertIn("Client A", direct_node.json()["error"])
                    self.assertEqual(main_flow.read_text(encoding="utf-8"), original_main)

                    page.get_by_role("tab", name="Second").click()
                    expect(page.locator("#edit-lock-message")).to_be_hidden()
                    page.get_by_role("button", name="Edit flow.py").click()
                    expect(flow_editor).to_be_editable()
                    edited_second = second_flow.read_text(encoding="utf-8") + "# Edited in browser\n"
                    flow_editor.fill(edited_second)
                    flow_dialog.get_by_role("button", name="Save", exact=True).click()
                    expect(flow_dialog.get_by_role("status")).to_contain_text("Saved.")
                    flow_dialog.get_by_role("button", name="Close").click()
                    self.assertEqual(second_flow.read_text(encoding="utf-8"), edited_second)

                    closed = call(portal, client_a, "close_sheet", workspace_id=workspace_id, sheet="Main", session_id=session_a)
                    self.assertFalse(closed.is_error, closed.content[0].text)

                    page.get_by_role("tab", name="Main").click()
                    expect(page.locator("#edit-lock-message")).to_be_hidden()
                    page.get_by_role("button", name="Edit flow.py").click()
                    expect(flow_editor).to_be_editable()
                    flow_dialog.get_by_role("button", name="Close").click()

    def test_web_editing_blocks_mcp_and_releases_when_editor_closes(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "WebLock"
            state = parent / "state"
            with application(state) as url:
                workspace_id = setup_workspace_with_two_sheets(page, url, state, root)
                token_a = issue_token(page, url, "Client A", [workspace_id])
                main_flow = root / "flow.py"
                second_flow = root / "flows" / "Second" / "flow.py"

                page.goto(f"{url}/workspaces/{workspace_id}/")
                flow = page.frame_locator('iframe[title="Main flow"]')
                expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()

                with portal.wrap_async_context_manager(mcp_client(url, token_a)) as client_a:
                    page.get_by_role("button", name="Edit flow.py").click()
                    flow_dialog = page.get_by_role("dialog", name="Edit flow.py")
                    flow_editor = flow_dialog.get_by_role("textbox", name="Source code")
                    expect(flow_editor).to_be_editable()

                    denied_open = call(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertTrue(denied_open.is_error)
                    self.assertIn("Web", denied_open.content[0].text)

                    readonly = call(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="read")
                    self.assertFalse(readonly.is_error, readonly.content[0].text)
                    self.assertTrue(output(readonly)["read_only"])

                    main_rev = output(call(portal, client_a, "read_file", workspace_id=workspace_id, path="flow.py", sheet="Main"))["revision"]
                    denied_write = call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="flow.py", revision=main_rev,
                        source=main_flow.read_text(encoding="utf-8") + "\n", sheet="Main",
                    )
                    self.assertTrue(denied_write.is_error)
                    self.assertIn("Web", denied_write.content[0].text)

                    opened_second = call(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Second", mode="edit")
                    self.assertFalse(opened_second.is_error, opened_second.content[0].text)
                    session_second = output(opened_second)["session_id"]
                    second_rev = output(call(portal, client_a, "read_file", workspace_id=workspace_id, path="flow.py", sheet="Second"))["revision"]
                    wrote_second = call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="flow.py", revision=second_rev,
                        source=second_flow.read_text(encoding="utf-8") + "# MCP on Second\n",
                        sheet="Second", session_id=session_second,
                    )
                    self.assertFalse(wrote_second.is_error, wrote_second.content[0].text)
                    self.assertFalse(call(portal, client_a, "close_sheet", workspace_id=workspace_id, sheet="Second", session_id=session_second).is_error)

                    second_context = page.context.browser.new_context()
                    page2 = second_context.new_page()
                    try:
                        sign_in(page2, url, state)
                        page2.goto(f"{url}/workspaces/{workspace_id}/")
                        expect(page2.locator("#edit-lock-message")).to_be_visible()
                        expect(page2.locator("#edit-lock-message")).to_contain_text("Web editor")
                        page2.get_by_role("button", name="Edit flow.py").click()
                        flow_dialog2 = page2.get_by_role("dialog", name="Edit flow.py")
                        expect(flow_dialog2.get_by_role("textbox", name="Source code")).not_to_be_editable()
                        expect(flow_dialog2.get_by_role("button", name="Save", exact=True)).to_be_disabled()
                        flow_dialog2.get_by_role("button", name="Close").click()

                        edited_main = main_flow.read_text(encoding="utf-8") + "# Web saved\n"
                        flow_editor.fill(edited_main)
                        flow_dialog.get_by_role("button", name="Save", exact=True).click()
                        expect(flow_dialog.get_by_role("status")).to_contain_text("Saved.")
                        flow_dialog.get_by_role("button", name="Close").click()
                        self.assertEqual(main_flow.read_text(encoding="utf-8"), edited_main)

                        expect(page2.locator("#edit-lock-message")).to_be_hidden()
                    finally:
                        second_context.close()

                    reopened = wait_for_open_sheet(portal, client_a, workspace_id, "Main")
                    self.assertFalse(reopened.is_error, reopened.content[0].text)
                    self.assertFalse(call(portal, client_a, "close_sheet", workspace_id=workspace_id, sheet="Main", session_id=output(reopened)["session_id"]).is_error)

                    flow.get_by_role("button", name="Inspect Start").click()
                    flow.get_by_role("button", name="View files").click()
                    node_dialog = flow.get_by_role("dialog", name="Start files")
                    node_editor = node_dialog.get_by_role("textbox", name="Source code")
                    expect(node_editor).to_be_editable()

                    denied_during_node = call(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertTrue(denied_during_node.is_error)
                    self.assertIn("Web", denied_during_node.content[0].text)

                    updated_node = (root / "nodes/start/entrypoint.py").read_text(encoding="utf-8").replace(
                        "Hello from Workspace", "Saved from node editor",
                    )
                    node_editor.fill(updated_node)
                    node_dialog.get_by_role("button", name="Save", exact=True).click()
                    expect(node_dialog.locator("#node-source-message")).to_contain_text("Saved.")
                    node_dialog.get_by_role("button", name="Close node files").click()

                    after_node = wait_for_open_sheet(portal, client_a, workspace_id, "Main")
                    self.assertFalse(after_node.is_error, after_node.content[0].text)
                    self.assertFalse(call(portal, client_a, "close_sheet", workspace_id=workspace_id, sheet="Main", session_id=output(after_node)["session_id"]).is_error)

    def test_close_mcp_session_cleans_up_processes_on_success_failure_and_cancellation(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "Cleanup"
            state = parent / "state"
            with application(state) as url:
                workspace_id = setup_workspace_with_two_sheets(page, url, state, root)
                token_a = issue_token(page, url, "Client A", [workspace_id])
                token_b = issue_token(page, url, "Client B", [workspace_id])

                spawn_helper = (
                    "from pathlib import Path\n"
                    "import subprocess, sys\n"
                    "proc = subprocess.Popen([sys.executable, '-c', "
                    "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
                )

                with (
                    portal.wrap_async_context_manager(mcp_client(url, token_a)) as client_a,
                    portal.wrap_async_context_manager(mcp_client(url, token_b)) as client_b,
                ):
                    opened = call(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    session_a = output(opened)["session_id"]
                    rev = output(call(portal, client_a, "read_file", workspace_id=workspace_id, path="flow.py", sheet="Main"))["revision"]
                    valid_source = (
                        spawn_helper
                        + "Path('ok-pid').write_text(str(proc.pid), encoding='utf-8')\n"
                        + "Path('ok-exe').write_text(sys.executable, encoding='utf-8')\n"
                        + "from workspace_sdk import Flow, Output, Step\n"
                        + "from nodes.start.entrypoint import start\n"
                        + "flow = Flow((), (Step('Start', start, {}),), Output('Start'))\n"
                    )
                    written = call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="flow.py", revision=rev,
                        source=valid_source, sheet="Main", session_id=session_a,
                    )
                    self.assertFalse(written.is_error, written.content[0].text)
                    rev = output(written)["revision"]

                    checked = call(portal, client_a, "validate_flow", workspace_id=workspace_id, sheet="Main", session_id=session_a)
                    self.assertFalse(checked.is_error, checked.content[0].text)
                    self.assertTrue(output(checked)["valid"])
                    self.assertEqual((root / "ok-exe").read_text(encoding="utf-8"), str(root / ".venv/bin/python"))
                    ok_pid = int((root / "ok-pid").read_text(encoding="utf-8"))
                    wait_for(lambda: process_exited(ok_pid))
                    closed = call(portal, client_a, "close_sheet", workspace_id=workspace_id, sheet="Main", session_id=session_a, outcome="succeeded")
                    self.assertFalse(closed.is_error, closed.content[0].text)

                    reopened = call(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    session_a2 = output(reopened)["session_id"]
                    fail_source = (
                        spawn_helper
                        + "Path('fail-pid').write_text(str(proc.pid), encoding='utf-8')\n"
                        + "raise RuntimeError('validation boom')\n"
                    )
                    written = call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="flow.py", revision=rev,
                        source=fail_source, sheet="Main", session_id=session_a2,
                    )
                    rev = output(written)["revision"]
                    failed = call(portal, client_a, "validate_flow", workspace_id=workspace_id, sheet="Main", session_id=session_a2)
                    self.assertFalse(failed.is_error, failed.content[0].text)
                    self.assertFalse(output(failed)["valid"])
                    fail_pid = int((root / "fail-pid").read_text(encoding="utf-8"))
                    wait_for(lambda: process_exited(fail_pid))
                    closed_fail = call(portal, client_a, "close_sheet", workspace_id=workspace_id, sheet="Main", session_id=session_a2, outcome="failed")
                    self.assertFalse(closed_fail.is_error, closed_fail.content[0].text)

                    reopened_b = call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(reopened_b.is_error, reopened_b.content[0].text)
                    session_b = output(reopened_b)["session_id"]
                    hang_source = (
                        spawn_helper
                        + "import time\n"
                        + "Path('hang-pid').write_text(str(proc.pid), encoding='utf-8')\n"
                        + "Path('hang-started').touch()\n"
                        + "time.sleep(60)\n"
                    )
                    written = call(
                        portal, client_b, "write_file",
                        workspace_id=workspace_id, path="flow.py", revision=rev,
                        source=hang_source, sheet="Main", session_id=session_b,
                    )
                    rev = output(written)["revision"]

                    outcomes = []
                    def run_blocking_validate():
                        try:
                            outcomes.append(call(portal, client_b, "validate_flow", workspace_id=workspace_id, sheet="Main", session_id=session_b))
                        except BaseException as exc:
                            outcomes.append(exc)

                    worker = Thread(target=run_blocking_validate, daemon=True)
                    worker.start()
                    wait_for(lambda: (root / "hang-started").is_file() and (root / "hang-pid").is_file())
                    hang_pid = int((root / "hang-pid").read_text(encoding="utf-8"))
                    self.assertFalse(process_exited(hang_pid))

                    closed_cancel = call(portal, client_b, "close_sheet", workspace_id=workspace_id, sheet="Main", session_id=session_b, outcome="cancelled")
                    self.assertFalse(closed_cancel.is_error, closed_cancel.content[0].text)
                    self.assertTrue(process_exited(hang_pid))
                    worker.join(timeout=5)
                    self.assertFalse(worker.is_alive())

                    (root / "hang-started").unlink()
                    (root / "hang-pid").unlink()

                    opened_abort = call(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(opened_abort.is_error, opened_abort.content[0].text)
                    session_abort = output(opened_abort)["session_id"]

                    async def abort_on_spawn():
                        async with mcp_client(url, token_a) as async_client:
                            async with anyio.create_task_group() as tg:
                                async def invoke():
                                    await async_client.call_tool(
                                        "validate_flow",
                                        {"workspace_id": workspace_id, "sheet": "Main", "session_id": session_abort},
                                    )
                                tg.start_soon(invoke)
                                while not ((root / "hang-started").is_file() and (root / "hang-pid").is_file()):
                                    await anyio.sleep(0.02)
                                tg.cancel_scope.cancel()

                    portal.call(abort_on_spawn)
                    abort_pid = int((root / "hang-pid").read_text(encoding="utf-8"))
                    wait_for(lambda: process_exited(abort_pid))
                    closed_abort = call(portal, client_a, "close_sheet", workspace_id=workspace_id, sheet="Main", session_id=session_abort, outcome="cancelled")
                    self.assertFalse(closed_abort.is_error, closed_abort.content[0].text)
                    check_unlocked = call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(check_unlocked.is_error, check_unlocked.content[0].text)
                    self.assertFalse(call(portal, client_b, "close_sheet", workspace_id=workspace_id, sheet="Main", session_id=output(check_unlocked)["session_id"]).is_error)

    def test_handed_off_run_continues_after_mcp_edit_session_closes(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "Handoff"
            state = parent / "state"
            with application(state) as url:
                workspace_id = setup_workspace_with_two_sheets(page, url, state, root)
                token_a = issue_token(page, url, "Client A", [workspace_id])
                token_b = issue_token(page, url, "Client B", [workspace_id])

                with (
                    portal.wrap_async_context_manager(mcp_client(url, token_a)) as client_a,
                    portal.wrap_async_context_manager(mcp_client(url, token_b)) as client_b,
                ):
                    opened = call(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(opened.is_error, opened.content[0].text)
                    session_a = output(opened)["session_id"]

                    node_read = output(call(
                        portal, client_a, "read_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py", sheet="Main",
                    ))
                    node_source = (
                        "from pathlib import Path\n"
                        "import subprocess, sys\n"
                        "from time import monotonic, sleep\n\n"
                        "def start(label):\n"
                        "    print(f'Running {label}', flush=True)\n"
                        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                        "    Path('run-child-pid').write_text(str(child.pid), encoding='utf-8')\n"
                        "    Path('run-started').touch()\n"
                        "    try:\n"
                        "        deadline = monotonic() + 20\n"
                        "        while not Path('release-run').exists() and monotonic() < deadline:\n"
                        "            sleep(0.02)\n"
                        "        return {'label': label, 'done': True}\n"
                        "    finally:\n"
                        "        if child.poll() is None:\n"
                        "            child.terminate()\n"
                        "            child.wait()\n"
                    )
                    self.assertFalse(call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py",
                        revision=node_read["revision"], source=node_source,
                        sheet="Main", session_id=session_a,
                    ).is_error)

                    flow_read = output(call(portal, client_a, "read_file", workspace_id=workspace_id, path="flow.py", sheet="Main"))
                    flow_source = (
                        "from workspace_sdk import Flow, Input, Output, Step\n"
                        "from nodes.start.entrypoint import start\n"
                        "flow = Flow(('label',), (Step('Start', start, {'label': Input('label')}),), Output('Start'))\n"
                    )
                    self.assertFalse(call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="flow.py",
                        revision=flow_read["revision"], source=flow_source,
                        sheet="Main", session_id=session_a,
                    ).is_error)

                    validated = call(portal, client_a, "validate_flow", workspace_id=workspace_id, sheet="Main", session_id=session_a)
                    self.assertFalse(validated.is_error, validated.content[0].text)
                    self.assertTrue(output(validated)["valid"])

                    started = call(
                        portal, client_a, "run_flow",
                        workspace_id=workspace_id, sheet="Main", input={"label": "handed-off"},
                    )
                    self.assertFalse(started.is_error, started.content[0].text)
                    run_id = output(started)["run_id"]
                    self.assertTrue(run_id)
                    wait_for(lambda: (root / "run-started").is_file() and (root / "run-child-pid").is_file())
                    child_pid = int((root / "run-child-pid").read_text(encoding="utf-8"))
                    self.assertFalse(process_exited(child_pid))

                    closed = call(
                        portal, client_a, "close_sheet",
                        workspace_id=workspace_id, sheet="Main", session_id=session_a, outcome="succeeded",
                    )
                    self.assertFalse(closed.is_error, closed.content[0].text)
                    self.assertTrue(output(closed)["released"])
                    self.assertFalse(process_exited(child_pid))

                    reopened_b = call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(reopened_b.is_error, reopened_b.content[0].text)
                    self.assertFalse(call(
                        portal, client_b, "close_sheet",
                        workspace_id=workspace_id, sheet="Main", session_id=output(reopened_b)["session_id"],
                    ).is_error)

                    readonly = output(call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="read"))
                    self.assertEqual(readonly["status"], "running")
                    self.assertEqual(readonly["run_id"], run_id)
                    self.assertFalse(readonly["read_only"])

                    page.goto(f"{url}/workspaces/{workspace_id}/")
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Running")
                    flow = page.frame_locator('iframe[title="Main flow"]')
                    expect(flow.get_by_label("stdout")).to_contain_text("Running handed-off")

                    (root / "release-run").touch()
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                    self.assertEqual(
                        json.loads(flow.locator("#result pre").inner_text()),
                        {"label": "handed-off", "done": True},
                    )
                    wait_for(lambda: process_exited(child_pid))
                    after_complete = output(call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="read"))
                    self.assertEqual(after_complete["status"], "succeeded")
                    self.assertEqual(after_complete["run_id"], run_id)

                    (root / "release-run").unlink()
                    (root / "run-started").unlink()
                    (root / "run-child-pid").unlink()
                    started_two = call(
                        portal, client_a, "run_flow",
                        workspace_id=workspace_id, sheet="Main", input={"label": "stop-me"},
                    )
                    self.assertFalse(started_two.is_error, started_two.content[0].text)
                    wait_for(lambda: (root / "run-started").is_file() and (root / "run-child-pid").is_file())
                    second_pid = int((root / "run-child-pid").read_text(encoding="utf-8"))
                    self.assertFalse(process_exited(second_pid))

                    stopped = call(portal, client_a, "stop_flow", workspace_id=workspace_id, sheet="Main")
                    self.assertFalse(stopped.is_error, stopped.content[0].text)
                    self.assertEqual(output(stopped)["status"], "stopped")
                    wait_for(lambda: process_exited(second_pid))
                    expect(page.get_by_role("tab", name="Main")).to_contain_text("Stopped")

