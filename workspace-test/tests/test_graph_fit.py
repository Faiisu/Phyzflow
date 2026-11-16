from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui


TELEMETRY = Path(__file__).resolve().parents[1] / "sheets-demo" / "flows" / "Telemetry Control Room"


class GraphFitTests(unittest.TestCase):
    def test_dragging_a_zoomed_node_moves_its_edges_and_keeps_source_access(self):
        with local_ui(TELEMETRY, single_flow=True) as (url, _), browser_page() as page:
            page.goto(url)
            page.get_by_role("button", name="Zoom in", exact=True).click()
            graph = page.get_by_role("region", name="Flow dependencies")
            node = graph.get_by_role("button", name="Inspect Run Parallel Pipeline", exact=True)
            neighbor = graph.get_by_role("button", name="Inspect Create Batch", exact=True)
            before = node.bounding_box()
            assert before is not None
            neighbor_before = neighbor.bounding_box()
            x = before["x"] + before["width"] / 2
            y = before["y"] + before["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 95, y + 120, steps=8)

            def edge_leaves_node(button):
                return button.evaluate("""async button => {
                    await new Promise(requestAnimationFrame);
                    const card = button.closest(".node");
                    const canvas = document.querySelector("#graph canvas");
                    const x = card.offsetLeft + card.offsetWidth + 7 - canvas.offsetLeft;
                    const y = card.offsetTop + card.offsetHeight / 2 - canvas.offsetTop;
                    const scale = canvas.width / canvas.offsetWidth;
                    const pixels = canvas.getContext("2d").getImageData(
                        Math.floor(x * scale), Math.floor(y * scale) - 2, 2, 5
                    ).data;
                    return Math.max(...pixels.filter((_, index) => index % 4 === 3)) > 0;
                }""")

            try:
                expect(node).to_have_attribute("aria-pressed", "true")
                self.assertTrue(edge_leaves_node(node))
            finally:
                page.mouse.up()
            after = node.bounding_box()
            assert after is not None
            self.assertAlmostEqual(after["x"] - before["x"], 95, delta=2)
            self.assertAlmostEqual(after["y"] - before["y"], 120, delta=2)
            self.assertEqual(neighbor.bounding_box(), neighbor_before)

            dialog = page.get_by_role("dialog", name="Run Parallel Pipeline files", exact=True)
            expect(dialog).to_be_hidden()
            node.dblclick()
            expect(dialog.locator("#node-source-name")).to_contain_text("nodes/stream_pipeline/entrypoint.py")

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
