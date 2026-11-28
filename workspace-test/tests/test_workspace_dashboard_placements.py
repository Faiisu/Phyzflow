import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from browser_support import browser_page, local_ui
from playwright.sync_api import expect
from test_application import application, sign_in
from test_application_lifecycle import create_workspace, wait_for
from workspace_sdk.sheets import create_sheet


class WorkspaceDashboardPlacementTests(unittest.TestCase):
    def test_local_workspace_ui_keeps_placement_and_layout_after_restart(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            cli = Path(sys.executable).with_name("workspace")
            subprocess.run([cli, "init", "--with-main"], cwd=root, check=True, capture_output=True)
            panel_path = root / "dashboards/workflow/index.html"
            panel_path.parent.mkdir(parents=True)
            panel_path.write_text("<h1>Local workflow panel</h1>", encoding="utf-8")

            dashboard = page.get_by_role("button", name="Dashboard", exact=True)
            board = page.get_by_role("dialog", name="Workspace dashboard").frame_locator(
                'iframe[title="Workspace Dashboard"]'
            )
            panel = board.get_by_role("region", name="Main · Workflow · Overview", exact=True)
            heading = panel.frame_locator("iframe").get_by_role("heading", name="Local workflow panel")

            with local_ui(root) as (url, _):
                page.goto(url)
                dashboard.click()
                expect(board.get_by_text("Add panels from this Workspace.")).to_be_visible()
                board.get_by_text("Panels", exact=True).click()
                board.get_by_role("button", name="Add Main · Workflow · Overview", exact=True).click()
                expect(heading).to_be_visible()
                handle = panel.get_by_label("Move or resize dashboard")
                width = panel.bounding_box()["width"] - 20
                handle.press("Shift+ArrowLeft")
                expect(panel).to_have_css("width", f"{int(width)}px")
                layout_url = f"{url}/dashboard/visualize-layout"
                wait_for(lambda: any(bounds["width"] == width
                                     for bounds in page.request.get(layout_url).json()["panels"].values()))

            with local_ui(root) as (url, _):
                page.goto(url)
                dashboard.click()
                expect(heading).to_be_visible()
                expect(panel).to_have_css("width", f"{int(width)}px")
                layout_url = f"{url}/dashboard/visualize-layout"
                original = page.request.get(layout_url).json()["panels"]
                key, saved = next(iter(original.items()))
                page.set_viewport_size({"width": 300, "height": 640})
                viewport = board.locator(".graph-scroll")
                expect(viewport).to_have_attribute("data-layout-fitted", "")
                self.assertTrue(viewport.evaluate("el => el.clientWidth === el.scrollWidth"))
                self.assertEqual(page.request.get(layout_url).json()["panels"], original)

                resize = panel.locator('[data-dash="resize"]')
                expect(resize).to_be_visible()
                resize.scroll_into_view_if_needed()
                box = resize.bounding_box()
                x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x, y + 40, steps=4)
                page.mouse.up()
                wait_for(lambda: page.request.get(layout_url).json()["panels"][key] == {
                    **saved, "height": saved["height"] + 40,
                })
                expect(panel).to_have_css("height", f"{saved['height'] + 40}px")
                page.set_viewport_size({"width": 1440, "height": 900})
                expect(panel).to_have_css("width", f"{int(width)}px")
                expect(panel).to_have_css("height", f"{saved['height'] + 40}px")

                page.set_viewport_size({"width": 390, "height": 844})
                expect(viewport).to_have_attribute("data-layout-fitted", "")
                narrow_width = int(panel.bounding_box()["width"])
                handle.press("Shift+ArrowLeft")
                handle.press("ArrowRight")
                wait_for(lambda: page.request.get(layout_url).json()["panels"][key] == {
                    **saved, "left": saved["left"] + 20, "width": narrow_width - 20, "height": saved["height"] + 40,
                })
                expect(panel).to_have_css("left", f"{saved['left'] + 20}px")
                expect(panel).to_have_css("width", f"{narrow_width - 20}px")

    def test_sheet_placements_keep_source_identity_and_layout_after_restart(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            state, root = parent / "state", parent / "workspace"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                second = create_sheet(root, "Second Sheet").parent
                workflow = root / "dashboards/workflow"
                node = second / "dashboards/nodes/Start"
                legacy = root / "dashboards/workspace"
                for folder, heading in ((workflow, "First source"), (node, "Second source"), (legacy, "Standalone source")):
                    folder.mkdir(parents=True)
                    (folder / "index.html").write_text(f"<h1>{heading}</h1>", encoding="utf-8")
                workspace.click()

                dashboard = page.get_by_role("button", name="Dashboard", exact=True)
                dialog = page.get_by_role("dialog", name="Workspace dashboard")
                board = dialog.frame_locator('iframe[title="Workspace Dashboard"]')
                menu = board.get_by_text("Panels", exact=True)
                dashboard.click()
                expect(board.get_by_text("Add panels from this Workspace.")).to_be_visible()
                expect(board.locator(".dashboard-panel:visible")).to_have_count(0)
                menu.click()
                board.get_by_role("button", name="Add Main · Workflow · Overview", exact=True).click()
                board.get_by_role("button", name="Add Second Sheet · Start · Overview", exact=True).click()
                first = board.get_by_role("region", name="Main · Workflow · Overview", exact=True)
                second_panel = board.get_by_role("region", name="Second Sheet · Start · Overview", exact=True)
                expect(first.frame_locator("iframe").get_by_role("heading", name="First source")).to_be_visible()
                expect(second_panel.frame_locator("iframe").get_by_role("heading", name="Second source")).to_be_visible()
                expect(first.locator("iframe")).to_have_attribute("sandbox", "allow-scripts allow-forms")

                toggle = board.get_by_role("button", name="Second Sheet · Start · Overview", exact=True)
                toggle.click()
                expect(second_panel).to_be_hidden()
                toggle.click()
                expect(second_panel).to_be_visible()
                board.get_by_role("button", name="Full screen dashboard", exact=True).click()
                expect(board.locator("html:fullscreen")).to_have_count(1)
                board.get_by_role("button", name="Exit full screen dashboard", exact=True).click()
                menu.click()
                first.get_by_role("button", name="Full screen", exact=True).click()
                expect(board.locator(".dashboard-panel:fullscreen")).to_have_count(1)
                first.get_by_role("button", name="Exit full screen", exact=True).click()
                layout_handle = first.get_by_label("Move or resize dashboard")
                width = first.bounding_box()["width"]
                layout_handle.press("Shift+ArrowLeft")
                expect(first).to_have_css("width", f"{int(width) - 20}px")
                layout_url = f"{page.url.rstrip('/')}/dashboard/visualize-layout"
                wait_for(lambda: any(bounds["width"] == width - 20
                                     for bounds in page.request.get(layout_url).json()["panels"].values()))
                first_bounds = first.bounding_box()
                second_bounds = second_panel.bounding_box()
                workspace_path = page.url.removeprefix(url)

                second_panel.get_by_role("link", name="Open source sheet").click()
                expect(dialog).to_be_hidden()
                expect(page.get_by_role("tab", name="Second Sheet")).to_have_attribute("aria-selected", "true")
                expect(page.frame_locator('iframe[title="Second Sheet flow"]').get_by_role(
                    "button", name="Inspect Start",
                )).to_be_visible()
                page.get_by_role("button", name="Rename sheet").click()
                page.get_by_role("dialog", name="Rename sheet").get_by_label("Sheet name").fill("Renamed Sheet")
                page.get_by_role("dialog", name="Rename sheet").get_by_role("button", name="Rename sheet").click()
                expect(page.get_by_role("tab", name="Renamed Sheet")).to_be_visible()

            with application(state) as url:
                sign_in(page, url, state)
                page.goto(f"{url}{workspace_path}")
                dashboard.click()
                renamed = board.get_by_role("region", name="Renamed Sheet · Start · Overview", exact=True)
                expect(renamed.frame_locator("iframe").get_by_role("heading", name="Second source")).to_be_visible()
                self.assertEqual(first.bounding_box(), first_bounds)
                self.assertEqual(renamed.bounding_box(), second_bounds)
                self.assertEqual((legacy / "index.html").read_text(encoding="utf-8"), "<h1>Standalone source</h1>")

                flow = page.frame_locator('iframe[title="Renamed Sheet flow"]')
                source_panel = flow.get_by_role("region", name="Start dashboard", exact=True)
                source_heading = source_panel.frame_locator("iframe").get_by_role("heading", name="Updated source")
                editor = flow.locator("#dashboard-editor")
                renamed.get_by_role("link", name="Open source sheet").click()
                flow.locator('[data-vis-node="Start"][data-vis-panel="default"]').click()
                expect(source_panel.frame_locator("iframe").get_by_role("heading", name="Second source")).to_be_visible()
                source_panel.get_by_role("button", name="Edit dashboard").click()
                editor.get_by_role("textbox", name="Source code").fill("<h1>Updated source</h1>")
                editor.get_by_role("button", name="Save", exact=True).click()
                expect(editor.get_by_role("status")).to_have_text("Saved.")
                editor.get_by_role("button", name="Close dashboard editor").click()
                expect(source_heading).to_be_visible()

                dashboard.click()
                renamed.get_by_role("button", name="Reload dashboard").click()
                expect(renamed.frame_locator("iframe").get_by_role("heading", name="Updated source")).to_be_visible()

                renamed.get_by_role("link", name="Open source sheet").click()
                source_panel.get_by_role("button", name="Remove dashboard").click()
                editor.get_by_role("button", name="Remove dashboard", exact=True).click()
                expect(source_panel).to_have_count(0)
                dashboard.click()
                expect(renamed.get_by_text("Source panel unavailable.")).to_be_visible()
                expect(first.frame_locator("iframe").get_by_role("heading", name="First source")).to_be_visible()

                menu.click()
                board.get_by_role("button", name="Remove placement: Main · Workflow · Overview").click()
                expect(first).to_have_count(0)
                board.get_by_role("button", name="Back to sheets").click()
                page.get_by_role("tab", name="Main", exact=True).click()
                main_flow = page.frame_locator('iframe[title="Main flow"]')
                main_flow.locator('[data-vis-node=""][data-vis-panel="default"]').click()
                expect(main_flow.get_by_role("region", name="Workflow dashboard", exact=True).frame_locator(
                    "iframe"
                ).get_by_role("heading", name="First source")).to_be_visible()
                self.assertEqual((workflow / "index.html").read_text(encoding="utf-8"), "<h1>First source</h1>")
                placement_url = f"{page.url.rstrip('/')}/dashboard/placements"
                response = page.request.post(placement_url, headers={"Origin": url}, data={
                    "action": "add", "sheet": str(root.parent / "another-workspace"), "scope": None, "id": "default",
                })
                self.assertEqual(response.status, 404)
