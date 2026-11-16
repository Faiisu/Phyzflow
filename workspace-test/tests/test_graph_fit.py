from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui


TELEMETRY = Path(__file__).resolve().parents[1] / "sheets-demo" / "flows" / "Telemetry Control Room"


class GraphFitTests(unittest.TestCase):
    def test_fit_graph_and_selection_keep_node_source_access(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            project = Path(temporary) / "Telemetry Control Room"
            copytree(TELEMETRY, project, ignore=ignore_patterns(".workspace", "__pycache__"))
            with local_ui(project, single_flow=True) as (url, _):
                page.goto(url)
                graph = page.get_by_role("region", name="Flow dependencies")
                fit_graph = page.get_by_role("button", name="Fit graph", exact=True)
                fit_selection = page.get_by_role("button", name="Fit selection", exact=True)

                page.get_by_role("complementary", name="Flow outline").get_by_role(
                    "button", name="Persist Batch", exact=False,
                ).click()
                selected = graph.get_by_role("button", name="Inspect Persist Batch", exact=True)
                expect(selected).to_have_attribute("aria-pressed", "true")
                fit_graph.click()
                whole_view = selected.bounding_box()
                assert whole_view is not None

                def cards_fit_viewport():
                    return graph.locator(".node").evaluate_all("""cards => cards.every(card => {
                        const box = card.getBoundingClientRect();
                        return box.left >= 0 && box.top >= 0 &&
                            box.right <= innerWidth && box.bottom <= innerHeight;
                    })""")

                self.assertTrue(cards_fit_viewport())
                fit_selection.click()
                close_view = selected.bounding_box()
                assert close_view is not None
                self.assertGreater(close_view["width"], whole_view["width"])

                source = page.locator("#inspector-source")
                expect(source).to_contain_text("nodes/persist/entrypoint.py:")
                expect(graph.locator(".node-source")).to_have_count(0)
                selected.dblclick()
                dialog = page.get_by_role("dialog", name="Persist Batch files", exact=True)
                expect(dialog).to_be_visible()
                expect(dialog.locator("#node-source-name")).to_contain_text("nodes/persist/entrypoint.py")
                dialog.get_by_role("button", name="Close node files").click()

                page.set_viewport_size({"width": 390, "height": 844})
                fit_graph.click()
                self.assertTrue(cards_fit_viewport())
                expect(selected).to_have_attribute("aria-pressed", "true")
