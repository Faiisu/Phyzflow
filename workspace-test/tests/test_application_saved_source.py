from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application, sign_in


class ApplicationSavedSourceTests(unittest.TestCase):
    def test_saved_source_takes_effect_only_on_the_next_run(self):
        for editor in ("browser", "filesystem"):
            with self.subTest(editor=editor), TemporaryDirectory() as temp, browser_page() as page:
                parent = Path(temp).resolve()
                root = parent / "Source updates"
                state = parent / "state"
                with application(state) as url:
                    sign_in(page, url, state)
                    page.get_by_role("button", name="Create Workspace").click()
                    page.get_by_label("New folder path").fill(str(root))
                    page.get_by_role("button", name="Create", exact=True).click()
                    workspace = page.get_by_role("link", name="Source updates")
                    expect(workspace).to_be_visible(timeout=20_000)
                    node = root / "nodes/start"
                    (node / "entrypoint.py").write_text(
                        '''from pathlib import Path
from time import monotonic, sleep


def start():
    print("Waiting for source edits")
    deadline = monotonic() + 20
    while not Path("finish-now").exists():
        if monotonic() >= deadline:
            raise TimeoutError()
        sleep(.02)
    from .detail import LABEL as node_label
    from local_detail import LABEL as local_label
    return {"node": node_label, "local": local_label}
''',
                        encoding="utf-8",
                    )
                    flow_path = root / "flow.py"
                    flow_source = '''from workspace_sdk import Flow, Output, Step
from nodes.start.entrypoint import start


def finish(detail):
    return {"flow": "original", **detail}


flow = Flow((), (Step("Start", start, {}), Step("Finish", finish, {"detail": Output("Start")})), Output("Finish"))
'''
                    flow_path.write_text(flow_source, encoding="utf-8")
                    node_path = node / "detail.py"
                    local_path = root / "local_detail.py"
                    helper_source = 'LABEL = "original"\n'
                    node_path.write_text(helper_source, encoding="utf-8")
                    local_path.write_text(helper_source, encoding="utf-8")
                    workspace.click()

                    flow = page.frame_locator('iframe[title="Main flow"]')
                    run = flow.get_by_role("button", name="Run flow")
                    tab = page.get_by_role("tab", name="Main")
                    run.click()
                    console = flow.get_by_role("region", name="Run console")
                    expect(console.get_by_label("stdout")).to_contain_text("Waiting for source edits")
                    expect(tab).to_contain_text("Running")

                    edited_flow = flow_source.replace("original", "updated")
                    edited_helper = helper_source.replace("original", "updated")
                    local_path.write_text(edited_helper, encoding="utf-8")
                    if editor == "browser":
                        page.get_by_role("button", name="Edit flow.py").click()
                        flow_editor = page.get_by_role("dialog", name="Edit flow.py")
                        flow_input = flow_editor.get_by_role("textbox", name="Source code")
                        expect(flow_input).to_have_value(flow_source)
                        expect(flow_input).to_be_editable(timeout=2_000)
                        flow_input.fill(edited_flow)
                        save = flow_editor.get_by_role("button", name="Save", exact=True)
                        expect(save).to_be_enabled(timeout=2_000)
                        save.click()
                        expect(flow_editor.get_by_role("status")).to_contain_text("Saved.")
                        flow_editor.get_by_role("button", name="Close").click()

                        flow.get_by_role("button", name="Inspect Start").dblclick()
                        node_editor = flow.get_by_role("dialog", name="Start files")
                        node_editor.get_by_role("button", name="detail.py").click()
                        node_input = node_editor.get_by_role("textbox", name="Source code")
                        expect(node_input).to_have_value(helper_source)
                        expect(node_input).to_be_editable(timeout=2_000)
                        node_input.fill(edited_helper)
                        save = node_editor.get_by_role("button", name="Save", exact=True)
                        expect(save).to_be_enabled(timeout=2_000)
                        save.click()
                        expect(node_editor.locator("#node-source-message")).to_contain_text("Saved.")
                        node_editor.get_by_role("button", name="Close node files").click()
                    else:
                        flow_path.write_text(edited_flow, encoding="utf-8")
                        node_path.write_text(edited_helper, encoding="utf-8")

                    self.assertEqual(flow_path.read_text(), edited_flow)
                    self.assertEqual(node_path.read_text(), edited_helper)
                    expect(tab).to_contain_text("Running")
                    page.route("**/reload/**", lambda route: route.abort())
                    (root / "finish-now").touch()
                    result = flow.locator("#result pre")
                    expect(result).to_be_visible()
                    self.assertEqual(
                        json.loads(result.inner_text()),
                        {"flow": "original", "node": "original", "local": "original"},
                    )
                    run.click()
                    expect(result).to_contain_text('"updated"')
                    self.assertEqual(
                        json.loads(result.inner_text()),
                        {"flow": "updated", "node": "updated", "local": "updated"},
                    )
