import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application, sign_in
from test_application_lifecycle import create_workspace, wait_for
from workspace_sdk.sheets import create_sheet


class WorkspaceDashboardPlacementTests(unittest.TestCase):
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
