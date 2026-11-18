import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui, show_panel


FLOW = """\
from workspace_sdk import Flow, Output, Step
from helper import value

flow = Flow((), (Step("Original", value, {}),), Output("Original"))
"""


def write_sheet(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "flow.py").write_text(FLOW)
    (root / "helper.py").write_text("def value(): return 'original'\n")


def sheet_view(page):
    frame = page.locator('iframe[title="Main flow"]')
    return frame, frame.content_frame


def expect_result(view, value):
    result = view.locator("#result pre")
    expect(result).to_have_text(json.dumps(value))


class AutoReloadTests(unittest.TestCase):
    def test_external_saves_reload_only_the_edited_sheet(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp)
            write_sheet(root)
            write_sheet(root / "flows" / "Other")
            with local_ui(root) as (url, _):
                page.goto(url)
                frame, view = sheet_view(page)
                expect(view.get_by_role("button", name="Inspect Original")).to_be_visible()
                page.get_by_role("tab", name="Other").click()
                other = page.locator('iframe[title="Other flow"]')
                expect(other.content_frame.get_by_role("button", name="Inspect Original")).to_be_visible()
                page.get_by_role("tab", name="Main").click()
                (root / "flow.py").write_text(FLOW.replace("Original", "Updated"))
                expect(page.locator("#reload-message")).to_have_text("Source changed.")
                (root / "helper.py").write_text("def value(): return 'updated!'\n")
                expect(view.get_by_role("button", name="Inspect Updated")).to_be_visible()
                expect(frame).to_have_attribute("src", "/flows/0/revision/1/view/")
                expect(other).to_have_attribute("src", "/flows/1/view/")
                expect(view.locator("#status")).to_have_text("Ready")
                view.get_by_role("button", name="Run flow").click()
                expect_result(view, "updated!")

    def test_running_sheet_reloads_the_latest_save_after_recording_the_original_run(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp)
            write_sheet(root)
            helper = root / "helper.py"
            helper.write_text(
                "from pathlib import Path\nfrom time import sleep\n"
                "def value():\n"
                "    while not Path('release').exists(): sleep(.02)\n"
                "    Path('finished').write_text('original')\n"
                "    return 'original'\n"
            )
            with local_ui(root) as (url, _):
                page.goto(url)
                frame, view = sheet_view(page)
                view.get_by_role("button", name="Run flow").click()
                tab = page.get_by_role("tab", name="Main")
                expect(tab).to_contain_text("Running")
                helper.write_text("def value(): return 'intermediate'\n")
                expect(page.locator("#reload-flow")).to_be_disabled()
                helper.write_text("def value(): return 'latest'\n")
                expect(frame).to_have_attribute("src", "/flows/0/view/")
                (root / "release").touch()
                expect(frame).to_have_attribute("src", "/flows/0/revision/1/view/")
                self.assertEqual((root / "finished").read_text(), "original")
                history = page.request.get(f"{url}/flows/0/revision/1/view/history").json()
                self.assertEqual([run["status"] for run in history], ["succeeded"])
                view.get_by_role("button", name="Run flow").click()
                expect_result(view, "latest")

    def test_source_drafts_survive_external_saves_until_resolved(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp)
            write_sheet(root)
            with local_ui(root) as (url, _):
                page.goto(url)
                frame, view = sheet_view(page)
                expect(view.get_by_role("button", name="Inspect Original")).to_be_visible()
                page.get_by_role("button", name="Edit flow.py").click()
                dialog = page.get_by_role("dialog", name="Edit flow.py")
                editor = dialog.get_by_role("textbox", name="Source code")
                draft = FLOW + "# Flow editor draft\n"
                editor.fill(draft)
                (root / "helper.py").write_text("def value(): return 'first edit'\n")
                expect(dialog.locator("#source-change-message")).to_be_visible()
                expect(editor).to_have_value(draft)
                expect(frame).to_have_attribute("src", "/flows/0/view/")
                dialog.get_by_role("button", name="Save", exact=True).click()
                expect(dialog.get_by_role("status")).to_contain_text("Saved.")
                self.assertEqual((root / "flow.py").read_text(), draft)
                expect(frame).to_have_attribute("src", "/flows/0/revision/1/view/")
                dialog.get_by_role("button", name="Close", exact=True).click()

                view.get_by_role("button", name="Inspect Original").click()
                show_panel(view, "Inspector")
                view.get_by_role("button", name="View files").click()
                files = view.get_by_role("dialog", name="Original files")
                node_editor = files.get_by_role("textbox", name="Source code")
                node_draft = "def value(): return 'node editor draft'\n"
                node_editor.fill(node_draft)
                newest = draft.replace("Original", "Updated")
                (root / "flow.py").write_text(newest)
                expect(page.locator("#reload-message")).to_have_text("Source changed. Reload pending.")
                expect(node_editor).to_have_value(node_draft)
                expect(frame).to_have_attribute("src", "/flows/0/revision/1/view/")
                page.once("dialog", lambda prompt: prompt.accept())
                files.get_by_role("button", name="Close node files").click()
                expect(view.get_by_role("button", name="Inspect Updated")).to_be_visible()
                self.assertEqual((root / "helper.py").read_text(), "def value(): return 'first edit'\n")

    def test_invalid_source_is_attempted_once_and_next_save_recovers(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp)
            write_sheet(root)
            with local_ui(root) as (url, _):
                attempts = []
                page.on("request", lambda request: attempts.append(request.url) if "/reload/" in request.url else None)
                page.goto(url)
                frame, view = sheet_view(page)
                expect(view.get_by_role("button", name="Inspect Original")).to_be_visible()
                helper = root / "helper.py"
                helper.write_text("def broken(\n")
                expect(page.locator("#notice-message")).to_contain_text("Syntax:")
                expect(page.locator("#notice-message")).to_contain_text("helper.py:1")
                for _ in range(4):
                    page.wait_for_event("response", predicate=lambda response: response.url == f"{url}/workspace")
                self.assertEqual(len(attempts), 1)
                helper.write_text("def value(): return 'recovered'\n")
                expect(frame).to_have_attribute("src", "/flows/0/revision/2/view/")
                expect(view.get_by_role("button", name="Inspect Original")).to_be_visible()
                view.get_by_role("button", name="Run flow").click()
                expect_result(view, "recovered")
                self.assertEqual(len(attempts), 2)
