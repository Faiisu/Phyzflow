from pathlib import Path
import json
import os
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


def wait_for(check, timeout=8):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if check():
            return
        sleep(0.02)
    raise AssertionError("Timed out waiting for expected state.")


def set_lock_clock(sheet_root: Path, offset_seconds: int) -> None:
    clock = sheet_root / ".workspace" / "edit-lock-now"
    clock.parent.mkdir(parents=True, exist_ok=True)
    clock.write_text(str(offset_seconds), encoding="utf-8")


def process_exited(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


class ApplicationForceOpenTests(unittest.TestCase):
    def test_idle_heartbeat_retains_lock_until_timeout_or_unclean_disconnect_marks_possibly_stale(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "StaleLock"
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

                    page.goto(f"{url}/workspaces/{workspace_id}/")
                    flow = page.frame_locator('iframe[title="Main flow"]')
                    expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()
                    expect(page.locator("#edit-lock-message")).to_contain_text("Client A")
                    expect(page.locator("#edit-lock-message")).not_to_contain_text("Possibly stale")

                    for offset in (15, 30, 45, 60):
                        set_lock_clock(root, offset)
                        beat = call(
                            portal, client_a, "heartbeat_sheet",
                            workspace_id=workspace_id, sheet="Main", session_id=session_a,
                        )
                        self.assertFalse(beat.is_error, beat.content[0].text)
                        beat_data = output(beat)
                        self.assertTrue(beat_data["locked"])
                        self.assertFalse(beat_data["stale"])
                        denied_b = call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                        self.assertTrue(denied_b.is_error)

                    expect(page.locator("#edit-lock-message")).not_to_contain_text("Possibly stale")

                    set_lock_clock(root, 106)
                    expect(page.locator("#edit-lock-message")).to_contain_text("Possibly stale")
                    expect(page.locator("#edit-lock-message")).to_contain_text("Client A")

                    readonly = output(call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="read"))
                    self.assertTrue(readonly["edit_lock"]["stale"])
                    self.assertFalse(readonly["edit_lock"]["disconnected"])
                    self.assertTrue(readonly["read_only"])

                    second_root = root / "flows" / "Second"
                    opened_second = call(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Second", mode="edit")
                    self.assertFalse(opened_second.is_error, opened_second.content[0].text)
                    session_second = output(opened_second)["session_id"]
                    second_rev = output(call(portal, client_a, "read_file", workspace_id=workspace_id, path="flow.py", sheet="Second"))["revision"]
                    blocking_flow = (
                        "from pathlib import Path\n"
                        "import time\n"
                        "Path('disconnect-started').touch()\n"
                        "time.sleep(60)\n"
                    )
                    written = call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="flow.py", revision=second_rev,
                        source=blocking_flow, sheet="Second", session_id=session_second,
                    )
                    self.assertFalse(written.is_error, written.content[0].text)

                    async def drop_connection_during_validate():
                        async with mcp_client(url, token_a) as transient_client:
                            async with anyio.create_task_group() as tg:
                                async def invoke():
                                    await transient_client.call_tool(
                                        "validate_flow",
                                        {"workspace_id": workspace_id, "sheet": "Second", "session_id": session_second},
                                    )
                                tg.start_soon(invoke)
                                while not (second_root / "disconnect-started").is_file():
                                    await anyio.sleep(0.02)
                                tg.cancel_scope.cancel()

                    portal.call(drop_connection_during_validate)

                    page.get_by_role("tab", name="Second").click()
                    expect(page.locator("#edit-lock-message")).to_contain_text("Possibly stale")
                    expect(page.locator("#edit-lock-message")).to_contain_text("Disconnected")
                    expect(page.locator("#edit-lock-message")).to_contain_text("Client A")

                    second_read = output(call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Second", mode="read"))
                    self.assertTrue(second_read["edit_lock"]["stale"])
                    self.assertTrue(second_read["edit_lock"]["disconnected"])

    def test_force_open_dialog_shows_details_and_cancel_keeps_lock_intact(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "ForceOpenCancel"
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
                    heartbeat_iso = output(opened)["edit_lock"]["last_heartbeat"]
                    expected_heartbeat = heartbeat_iso.split(".", 1)[0].replace("T", " ").removesuffix("+00:00") + " UTC"

                    set_lock_clock(root, 46)
                    page.goto(f"{url}/workspaces/{workspace_id}/")
                    flow = page.frame_locator('iframe[title="Main flow"]')
                    expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()
                    expect(page.locator("#edit-lock-message")).to_contain_text("Possibly stale")
                    expect(page.get_by_role("button", name="Force Open")).to_be_visible()

                    page.get_by_role("button", name="Force Open").click()
                    confirm_dialog = page.get_by_role("dialog", name="Force open sheet?")
                    expect(confirm_dialog).to_be_visible()
                    expect(confirm_dialog).to_contain_text("MCP client (Client A)")
                    expect(confirm_dialog).to_contain_text(expected_heartbeat)
                    expect(confirm_dialog).to_contain_text("Possibly stale")
                    expect(confirm_dialog).to_contain_text("discard any unsaved edits")
                    expect(confirm_dialog).to_contain_text("latest saved source from disk")

                    confirm_dialog.get_by_role("button", name="Cancel").click()
                    expect(confirm_dialog).to_be_hidden()
                    expect(page.locator("#edit-lock-message")).to_contain_text("Possibly stale")

                    denied_b = call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertTrue(denied_b.is_error)

                    page.get_by_role("button", name="Edit flow.py").click()
                    flow_dialog = page.get_by_role("dialog", name="Edit flow.py")
                    expect(flow_dialog.get_by_role("textbox", name="Source code")).not_to_be_editable()
                    expect(flow_dialog.get_by_role("button", name="Save", exact=True)).to_be_disabled()
                    flow_dialog.get_by_role("button", name="Force Open").click()
                    expect(confirm_dialog).to_be_visible()
                    confirm_dialog.get_by_role("button", name="Cancel").click()
                    expect(confirm_dialog).to_be_hidden()
                    expect(flow_dialog.get_by_role("textbox", name="Source code")).not_to_be_editable()
                    flow_dialog.get_by_role("button", name="Close").click()

                    flow.get_by_role("button", name="Inspect Start").click()
                    flow.get_by_role("button", name="View files").click()
                    node_dialog = flow.get_by_role("dialog", name="Start files")
                    expect(node_dialog.get_by_role("textbox", name="Source code")).not_to_be_editable()
                    expect(node_dialog.get_by_role("button", name="Save", exact=True)).to_be_disabled()
                    node_dialog.get_by_role("button", name="Force Open").click()
                    expect(confirm_dialog).to_be_visible()
                    confirm_dialog.get_by_role("button", name="Cancel").click()
                    expect(confirm_dialog).to_be_hidden()
                    expect(node_dialog.get_by_role("textbox", name="Source code")).not_to_be_editable()
                    node_dialog.get_by_role("button", name="Close node files").click()

                    still_locked = output(call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="read"))
                    self.assertTrue(still_locked["read_only"])
                    self.assertTrue(still_locked["edit_lock"]["locked"])

    def test_force_open_cleans_up_processes_loads_saved_source_and_blocks_old_session(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "ForceOpenRecover"
            state = parent / "state"
            with application(state) as url:
                workspace_id = setup_workspace_with_two_sheets(page, url, state, root)
                token_a = issue_token(page, url, "Client A", [workspace_id])
                token_b = issue_token(page, url, "Client B", [workspace_id])

                with portal.wrap_async_context_manager(mcp_client(url, token_a)) as client_a:
                    opened = call(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(opened.is_error, opened.content[0].text)
                    session_a = output(opened)["session_id"]

                    node_read = output(call(
                        portal, client_a, "read_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py", sheet="Main",
                    ))
                    saved_node = node_read["content"].replace("Hello from Workspace", "Saved by Client A")
                    wrote_node = call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py",
                        revision=node_read["revision"], source=saved_node,
                        sheet="Main", session_id=session_a,
                    )
                    self.assertFalse(wrote_node.is_error, wrote_node.content[0].text)
                    saved_node_rev = output(wrote_node)["revision"]

                    flow_read = output(call(
                        portal, client_a, "read_file",
                        workspace_id=workspace_id, path="flow.py", sheet="Main",
                    ))
                    saved_flow = (
                        "from pathlib import Path\n"
                        "import subprocess, sys, time\n"
                        "if '--check' in sys.argv:\n"
                        "    proc = subprocess.Popen([sys.executable, '-c', "
                        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
                        "    Path('stuck-pid').write_text(str(proc.pid), encoding='utf-8')\n"
                        "    Path('stuck-started').touch()\n"
                        "    time.sleep(60)\n"
                        "from workspace_sdk import Flow, Output, Step\n"
                        "from nodes.start.entrypoint import start\n"
                        "flow = Flow((), (Step('Saved Step', start, {}),), Output('Saved Step'))\n"
                    )
                    wrote_flow = call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="flow.py",
                        revision=flow_read["revision"], source=saved_flow,
                        sheet="Main", session_id=session_a,
                    )
                    self.assertFalse(wrote_flow.is_error, wrote_flow.content[0].text)
                    saved_flow_rev = output(wrote_flow)["revision"]

                    unsaved_draft = saved_flow.replace("Saved Step", "Unsaved Draft Step")
                    staged = call(
                        portal, client_a, "stage_draft",
                        workspace_id=workspace_id, path="flow.py",
                        source=unsaved_draft, sheet="Main", session_id=session_a,
                    )
                    self.assertFalse(staged.is_error, staged.content[0].text)
                    self.assertTrue(output(staged)["edit_lock"]["has_unsaved_draft"])
                    self.assertEqual((root / "flow.py").read_text(encoding="utf-8"), saved_flow)

                    validate_outcomes = []

                    def run_stuck_validate():
                        try:
                            validate_outcomes.append(
                                call(portal, client_a, "validate_flow", workspace_id=workspace_id, sheet="Main", session_id=session_a)
                            )
                        except BaseException as exc:
                            validate_outcomes.append(exc)

                    worker = Thread(target=run_stuck_validate, daemon=True)
                    worker.start()
                    wait_for(lambda: (root / "stuck-started").is_file() and (root / "stuck-pid").is_file())
                    stuck_pid = int((root / "stuck-pid").read_text(encoding="utf-8"))
                    self.assertFalse(process_exited(stuck_pid))

                    set_lock_clock(root, 46)
                    page.goto(f"{url}/workspaces/{workspace_id}/")
                    expect(page.locator("#edit-lock-message")).to_contain_text("Possibly stale")
                    page.get_by_role("button", name="Force Open").click()
                    confirm_dialog = page.get_by_role("dialog", name="Force open sheet?")
                    expect(confirm_dialog).to_be_visible()
                    expect(confirm_dialog).to_contain_text("This session has unsaved edits")
                    confirm_dialog.get_by_role("button", name="Force Open").click()
                    expect(confirm_dialog).to_be_hidden()

                    wait_for(lambda: process_exited(stuck_pid))
                    worker.join(timeout=5)
                    self.assertFalse(worker.is_alive())
                    expect(page.locator("#edit-lock-message")).to_be_hidden()

                    flow = page.frame_locator('iframe[title="Main flow"]')
                    expect(flow.get_by_role("button", name="Inspect Saved Step")).to_be_visible()
                    expect(flow.get_by_role("button", name="Inspect Unsaved Draft Step")).to_have_count(0)

                    page.get_by_role("button", name="Edit flow.py").click()
                    flow_dialog = page.get_by_role("dialog", name="Edit flow.py")
                    flow_editor = flow_dialog.get_by_role("textbox", name="Source code")
                    expect(flow_editor).to_have_value(saved_flow)
                    expect(flow_editor).to_be_editable()
                    edited_flow = saved_flow.replace("Saved Step", "Browser Saved Step")
                    flow_editor.fill(edited_flow)
                    flow_dialog.get_by_role("button", name="Save", exact=True).click()
                    expect(flow_dialog.get_by_role("status")).to_contain_text("Saved.")
                    flow_dialog.get_by_role("button", name="Close").click()
                    self.assertEqual((root / "flow.py").read_text(encoding="utf-8"), edited_flow)

                    expect(flow.get_by_role("button", name="Inspect Browser Saved Step")).to_be_visible()

                with portal.wrap_async_context_manager(mcp_client(url, token_a)) as reconnected_a:
                    rejected_beat = call(
                        portal, reconnected_a, "heartbeat_sheet",
                        workspace_id=workspace_id, sheet="Main", session_id=session_a,
                    )
                    self.assertTrue(rejected_beat.is_error)

                    rejected_stage = call(
                        portal, reconnected_a, "stage_draft",
                        workspace_id=workspace_id, path="flow.py",
                        source=unsaved_draft, sheet="Main", session_id=session_a,
                    )
                    self.assertTrue(rejected_stage.is_error)

                    rejected_write = call(
                        portal, reconnected_a, "write_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py",
                        revision=saved_node_rev, source=saved_node.replace("Saved by Client A", "Hijacked"),
                        sheet="Main", session_id=session_a,
                    )
                    self.assertTrue(rejected_write.is_error)

                    rejected_patch = call(
                        portal, reconnected_a, "apply_patch",
                        workspace_id=workspace_id, path="flow.py",
                        revision=saved_flow_rev, old_text="Saved Step", new_text="Hijacked Step",
                        sheet="Main", session_id=session_a,
                    )
                    self.assertTrue(rejected_patch.is_error)

                    rejected_validate = call(
                        portal, reconnected_a, "validate_flow",
                        workspace_id=workspace_id, sheet="Main", session_id=session_a,
                    )
                    self.assertTrue(rejected_validate.is_error)

                    rejected_close = call(
                        portal, reconnected_a, "close_sheet",
                        workspace_id=workspace_id, sheet="Main", session_id=session_a,
                    )
                    self.assertTrue(rejected_close.is_error)

                    self.assertEqual((root / "flow.py").read_text(encoding="utf-8"), edited_flow)
                    self.assertEqual((root / "nodes/start/entrypoint.py").read_text(encoding="utf-8"), saved_node)

                with portal.wrap_async_context_manager(mcp_client(url, token_b)) as client_b:
                    opened_b = call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit")
                    self.assertFalse(opened_b.is_error, opened_b.content[0].text)
                    self.assertFalse(call(
                        portal, client_b, "close_sheet",
                        workspace_id=workspace_id, sheet="Main", session_id=output(opened_b)["session_id"],
                    ).is_error)

    def test_force_open_from_source_dialog_loads_saved_source_and_persists_invalidation_across_restart(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            root = parent / "ForceOpenDialog"
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
                    saved_node = node_read["content"].replace("Hello from Workspace", "Saved output")
                    staged_node = call(
                        portal, client_a, "stage_draft",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py",
                        source=node_read["content"].replace("Hello from Workspace", "Draft output"),
                        sheet="Main", session_id=session_a,
                    )
                    self.assertFalse(staged_node.is_error, staged_node.content[0].text)
                    self.assertTrue(output(staged_node)["edit_lock"]["has_unsaved_draft"])

                    wrote_node = call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py",
                        revision=node_read["revision"], source=saved_node,
                        sheet="Main", session_id=session_a,
                    )
                    self.assertFalse(wrote_node.is_error, wrote_node.content[0].text)
                    after_write_status = output(call(portal, client_b, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="read"))
                    self.assertFalse(after_write_status["edit_lock"]["has_unsaved_draft"])

                    flow_read = output(call(portal, client_a, "read_file", workspace_id=workspace_id, path="flow.py", sheet="Main"))
                    saved_flow = flow_read["content"].replace('"Start"', '"Saved Node"')
                    wrote_flow = call(
                        portal, client_a, "write_file",
                        workspace_id=workspace_id, path="flow.py",
                        revision=flow_read["revision"], source=saved_flow,
                        sheet="Main", session_id=session_a,
                    )
                    self.assertFalse(wrote_flow.is_error, wrote_flow.content[0].text)

                    page.goto(f"{url}/workspaces/{workspace_id}/")
                    flow = page.frame_locator('iframe[title="Main flow"]')
                    expect(flow.get_by_role("button", name="Inspect Saved Node")).to_be_visible()

                    page.get_by_role("button", name="Edit flow.py").click()
                    flow_dialog = page.get_by_role("dialog", name="Edit flow.py")
                    flow_editor = flow_dialog.get_by_role("textbox", name="Source code")
                    expect(flow_editor).not_to_be_editable()

                    staged_flow = call(
                        portal, client_a, "stage_draft",
                        workspace_id=workspace_id, path="flow.py",
                        source=saved_flow.replace('"Saved Node"', '"Discarded Node"'),
                        sheet="Main", session_id=session_a,
                    )
                    self.assertFalse(staged_flow.is_error, staged_flow.content[0].text)

                    set_lock_clock(root, 46)
                    expect(flow_dialog.get_by_role("status")).to_contain_text("Possibly stale")
                    flow_dialog.get_by_role("button", name="Force Open").click()
                    confirm_dialog = page.get_by_role("dialog", name="Force open sheet?")
                    expect(confirm_dialog).to_be_visible()
                    expect(confirm_dialog).to_contain_text("This session has unsaved edits")
                    confirm_dialog.get_by_role("button", name="Force Open").click()
                    expect(confirm_dialog).to_be_hidden()

                    expect(flow_editor).to_have_value(saved_flow)
                    expect(flow_editor).to_be_editable()

                    denied_b_during_web_edit = call(
                        portal, client_b, "open_sheet",
                        workspace_id=workspace_id, sheet="Main", mode="edit",
                    )
                    self.assertTrue(denied_b_during_web_edit.is_error)
                    self.assertIn("Web", denied_b_during_web_edit.content[0].text)

                    flow_dialog.get_by_role("button", name="Close").click()
                    expect(flow.get_by_role("button", name="Inspect Saved Node")).to_be_visible()
                    flow.get_by_role("button", name="Run flow").click()
                    result = flow.locator("#result pre")
                    expect(result).to_be_visible()
                    self.assertEqual(json.loads(result.inner_text()), {"message": "Saved output"})

            with application(state) as restarted_url:
                with portal.wrap_async_context_manager(mcp_client(restarted_url, token_a)) as old_after_restart:
                    rejected_after_restart = call(
                        portal, old_after_restart, "write_file",
                        workspace_id=workspace_id, path="flow.py",
                        revision=output(wrote_flow)["revision"],
                        source=saved_flow.replace('"Saved Node"', '"Zombie"'),
                        sheet="Main", session_id=session_a,
                    )
                    self.assertTrue(rejected_after_restart.is_error)
                    self.assertEqual((root / "flow.py").read_text(encoding="utf-8"), saved_flow)
