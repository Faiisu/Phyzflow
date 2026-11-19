from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application, sign_in
from test_application_lifecycle import create_workspace
from workspace_sdk.sheets import create_sheet


class ApplicationSavedSourceTests(unittest.TestCase):
    def test_repair_a_broken_node_file_after_reopening_the_workspace(self):
        for failing_run in (False, True):
            with self.subTest(failing_run=failing_run), TemporaryDirectory() as temp, browser_page() as page:
                parent = Path(temp).resolve()
                root = parent / "Recoverable"
                state = parent / "state"
                with application(state) as url:
                    workspace = create_workspace(page, url, state, root)
                    outside = parent / "outside.py"
                    outside.write_text("value = 1\n")
                    (root / "nodes/start/alias.py").symlink_to(root / "flow.py")
                    filename = "helper.py" if failing_run else "entrypoint.py"
                    relative = f"nodes/start/{filename}"
                    node = root / relative
                    if failing_run:
                        node.write_text("value = 1\n")
                    workspace.click()
                    flow = page.frame_locator('iframe[title="Main flow"]')
                    tab = page.get_by_role("tab", name="Main")
                    run = page.get_by_role("button", name="Run flow", exact=True)
                    workspace_url = page.url
                    reloads = []
                    page.on("request", lambda request: reloads.append(request.url) if "/reload/" in request.url else None)
                    flow.get_by_role("button", name="Inspect Start").dblclick()
                    dialog = flow.get_by_role("dialog", name="Start files")
                    dialog.get_by_role("button", name=filename, exact=True).click()
                    editor = dialog.get_by_role("textbox", name="Source code")
                    original = node.read_text()
                    expect(editor).to_have_value(original)
                    invalid = "def broken(\n"
                    editor.fill(invalid)
                    dialog.get_by_role("button", name="Save", exact=True).click()
                    expect(dialog.locator("#node-source-message")).to_contain_text("Saved.")
                    dialog.get_by_role("button", name="Close node files").click()
                    expect(page.locator("#reload-message")).to_contain_text("Source changed.")
                    for _ in range(4):
                        page.wait_for_event("response", predicate=lambda response: response.url == f"{workspace_url}workspace")
                    self.assertEqual(reloads, [])
                    expect(tab).to_contain_text("Ready")
                    expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()
                    expect(page.locator("#notice-message")).to_be_hidden()
                    expect(run).to_be_enabled()
                    if failing_run:
                        page.reload()
                        expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()
                        run.click()
                        expect(tab).to_contain_text("Failed")
                    else:
                        page.get_by_role("button", name="Reload flow").click()
                        expect(page.locator('iframe[title="Main recovery graph"]')).to_be_visible()
                        expect(page.locator("#recovery-hint")).to_be_hidden()
                        expect(page.locator("#notice-message")).to_be_hidden()
                        run.click()
                        expect(tab).to_contain_text("Failed")
                    expect(page.locator("#recovery-hint")).to_contain_text("Last loaded graph")
                    expect(page.locator("#notice-message")).to_contain_text(relative)
                    graph = page.frame_locator('iframe[title="Main recovery graph"]')
                    edit_node = graph.get_by_role("button", name="Edit Start source")
                    expect(edit_node).to_be_visible(timeout=3000)
                    expect(run).to_be_enabled()
                    if failing_run:
                        page.set_viewport_size({"width": 390, "height": 844})
                    else:
                        edit_node.click()
                        page.get_by_role("dialog", name="Edit source").get_by_role("button", name="Close").click()
                    page.reload()
                    expect(page.locator("#notice-message")).to_contain_text(relative)
                    routes = page.request.get(f"{page.url}workspace").json()
                    revision = routes["flows"][0]["revision"]
                    view = f"revision/{revision}/" if revision else ""
                    self.assertEqual(page.request.get(f"{page.url}flows/0/{view}view/").status, 503)
                    endpoint = f"{page.url}flows/0/source"
                    self.assertEqual(page.request.get(endpoint).status, 200)
                    files = page.request.get(f"{endpoint}?files").json()["files"]
                    self.assertNotIn("nodes/start/alias.py", files)
                    for path in ("../outside.py", "nodes/start/alias.py"):
                        self.assertEqual(page.request.get(f"{endpoint}?path={path}").status, 404)
                    rejected = page.request.post(
                        f"{endpoint}?path={relative}",
                        headers={"Origin": url},
                        data={"source": original, "revision": "0" * 64},
                    )
                    self.assertEqual(rejected.status, 409)
                    self.assertEqual(node.read_text(), invalid)
                    recovery = page.get_by_role("button", name="Edit source", exact=True)
                    expect(recovery).to_be_enabled(timeout=2000)
                    edit_node.click()
                    source = page.get_by_role("dialog", name="Edit source")
                    expect(source.locator("#flow-source-diagnostic")).to_contain_text(relative)
                    expect(source.get_by_label("Python file")).to_have_value(relative)
                    editor = source.get_by_role("textbox", name="Source code")
                    expect(editor).to_have_value(invalid)
                    editor.fill(original)
                    source.get_by_role("button", name="Save", exact=True).click()
                    expect(source.locator("#flow-source-message")).to_contain_text("Saved.")
                    self.assertEqual(node.read_text(), original)
                    source.get_by_role("button", name="Close", exact=True).click()
                    expect(run).to_be_enabled()
                    run.click()
                    expect(tab).to_contain_text("Succeeded")
                    expect(page.locator('iframe[title="Main recovery graph"]')).to_have_count(0)

    def test_graph_opens_before_run_with_invalid_source_and_survives_application_restart(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root, state = parent / "Source preview", parent / "state"
            node = root / "nodes/start/entrypoint.py"
            result = page.locator("#notice-message")
            run = page.get_by_role("button", name="Run flow", exact=True)
            tab = page.get_by_role("tab", name="Main")
            graph = page.frame_locator('iframe[title="Main recovery graph"]')
            edit_node = graph.get_by_role("button", name="Edit Start source")
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                flow = root / "flow.py"
                flow.write_text(flow.read_text().replace(
                    'steps=(Step("Start", start, {}),),\n    output=Output("Start"),',
                    'steps=(Step("Finish", lambda value: value, {"value": Output("Start")}),\n'
                    '           Step("Start", start, {})),\n    output=Output("Finish"),',
                ))
                create_sheet(root, "Broken definition").write_text("def invalid(\n")
                valid = node.read_text()
                node.write_text("def invalid(\n")
                workspace.click()
                expect(edit_node).to_be_visible()
                expect(graph.get_by_role("button", name="Edit Finish source")).to_be_visible()
                expect(graph.get_by_text("Depends on Start", exact=True)).to_have_count(1)
                expect(result).to_be_hidden()
                expect(tab).to_contain_text("Ready")
                run.click()
                expect(tab).to_contain_text("Failed")
                expect(result).to_contain_text("nodes/start/entrypoint.py:1")
                edit_node.click()
                source = page.get_by_role("dialog", name="Edit source")
                expect(source.get_by_label("Python file")).to_have_value("nodes/start/entrypoint.py")
                source.get_by_role("textbox", name="Source code").fill(valid)
                source.get_by_role("button", name="Save").click()
                expect(source.locator("#flow-source-message")).to_contain_text("Saved.")
                source.get_by_role("button", name="Close").click()
                run.click()
                expect(tab).to_contain_text("Succeeded")
                expect(page.frame_locator('iframe[title="Main flow"]').get_by_role("button", name="Inspect Start")).to_be_visible()
                page.get_by_role("tab", name="Broken definition").click()
                draft = page.frame_locator('iframe[title="Broken definition recovery graph"]')
                draft.get_by_role("button", name="Edit flow.py source").click()
                source = page.get_by_role("dialog", name="Edit source")
                expect(source.get_by_role("textbox", name="Source code")).to_have_value("def invalid(\n")
                expect(result).to_be_hidden()
                source.get_by_role("button", name="Close").click()

            flow.write_text("def invalid(\n")
            with application(state) as url:
                sign_in(page, url, state)
                page.get_by_role("link", name=root.name).click()
                expect(edit_node).to_be_visible()
                expect(page.locator("#recovery-hint")).to_be_hidden()
                expect(result).to_be_hidden()
                run.click()
                expect(page.locator("#recovery-hint")).to_contain_text("Last loaded graph")
                expect(result).to_contain_text("flow.py:1")

    def test_saved_source_takes_effect_only_on_the_next_run(self):
        for editor in ("browser", "filesystem"):
            with self.subTest(editor=editor), TemporaryDirectory() as temp, browser_page() as page:
                parent = Path(temp).resolve()
                root = parent / "Source updates"
                state = parent / "state"
                with application(state) as url:
                    sign_in(page, url, state)
                    page.get_by_role("button", name="Create Workspace").click()
                    page.get_by_role("button", name="Enter a folder path").click()
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
                    run = page.get_by_role("button", name="Run flow")
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
                    (root / "finish-now").touch()
                    result = flow.locator("#result pre")
                    expect(result).to_be_visible()
                    self.assertEqual(
                        json.loads(result.inner_text()),
                        {"flow": "original", "node": "original", "local": "original"},
                    )
                    expect(page.locator("#reload-message")).to_have_text("Source changed. Run to use the saved code.")
                    run.click()
                    expect(result).to_contain_text('"updated"')
                    self.assertEqual(
                        json.loads(result.inner_text()),
                        {"flow": "updated", "node": "updated", "local": "updated"},
                    )
