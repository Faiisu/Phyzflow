from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui


TELEMETRY = Path(__file__).resolve().parents[1] / "sheets-demo" / "flows" / "Telemetry Control Room"


class GraphDependenciesTests(unittest.TestCase):
    def test_connections_avoid_card_interiors_before_and_after_a_drag(self):
        with local_ui(TELEMETRY, single_flow=True) as (url, _), browser_page() as page:
            page.goto(url)
            graph = page.get_by_role("region", name="Flow dependencies")
            pipeline = graph.get_by_role("button", name="Inspect Run Parallel Pipeline", exact=True)
            pipeline.click()
            canvas = graph.locator("canvas")

            def card_intersections():
                return canvas.evaluate("""async canvas => {
                    await new Promise(requestAnimationFrame);
                    const ctx = canvas.getContext("2d");
                    const scale = canvas.width / canvas.offsetWidth;
                    return [...canvas.parentElement.querySelectorAll(".node")].filter(card => {
                        const x = (card.offsetLeft + 6 - canvas.offsetLeft) * scale;
                        const y = (card.offsetTop + 6 - canvas.offsetTop) * scale;
                        const pixels = ctx.getImageData(x, y, (card.offsetWidth - 12) * scale,
                            (card.offsetHeight - 12) * scale).data;
                        return pixels.some((value, index) => index % 4 === 3 && value > 0);
                    }).map(card => card.querySelector("h3").textContent);
                }""")

            self.assertEqual(card_intersections(), [])
            box = pipeline.bounding_box()
            reconcile = graph.get_by_role("button", name="Inspect Reconcile Events", exact=True).bounding_box()
            assert box is not None and reconcile is not None
            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 230, y + 200, steps=8)
            page.mouse.up()
            moved = pipeline.bounding_box()
            assert moved is not None
            self.assertGreater(moved["x"], reconcile["x"])
            self.assertEqual(card_intersections(), [])
            expect(graph.get_by_text("processed", exact=True).first).to_be_visible()

    def test_selecting_a_telemetry_node_shows_and_follows_its_dependencies(self):
        with local_ui(TELEMETRY, single_flow=True) as (url, _), browser_page() as page:
            page.goto(url)
            graph = page.get_by_role("region", name="Flow dependencies")
            inspector = page.get_by_role("region", name="Node inspector")
            graph.get_by_role("button", name="Inspect Persist Batch", exact=True).click()
            expect(inspector.locator("#inspector-name")).to_have_text("Persist Batch")
            self.assertCountEqual(
                graph.locator(".edge-label").all_text_contents(),
                ["batch", "audit", "aggregates", "processed", "receipt", "receipt"],
            )
            incoming = graph.locator('.edge-label[data-direction="in"]')
            outgoing = graph.locator('.edge-label[data-direction="out"]')
            expect(incoming).to_have_count(4)
            expect(outgoing).to_have_text(["receipt", "receipt"])
            incoming_color = incoming.first.evaluate("label => getComputedStyle(label).color")
            outgoing_color = outgoing.first.evaluate("label => getComputedStyle(label).color")
            self.assertNotEqual(incoming_color, outgoing_color)
            expect(inspector.locator("#inspector-inputs button").first).to_have_css("color", incoming_color)
            expect(inspector.locator("#inspector-feeds button").first).to_have_css("color", outgoing_color)

            audit = inspector.locator("#inspector-inputs").get_by_role("listitem").filter(has_text="audit")
            upstream = audit.get_by_role("button", name="Reconcile Events", exact=True)
            upstream.focus()
            upstream.press("Enter")
            selected = graph.get_by_role("button", name="Inspect Reconcile Events", exact=True)
            expect(selected).to_be_focused()
            expect(selected).to_have_attribute("aria-pressed", "true")
            expect(inspector.locator("#inspector-name")).to_have_text("Reconcile Events")
            expect(graph.locator(".edge-label").filter(has_text="audit").first).to_be_visible()

            feeds = inspector.locator("#inspector-feeds")
            feeds.get_by_role("button", name="Persist Batch", exact=True).click()
            expect(inspector.locator("#inspector-name")).to_have_text("Persist Batch")

            page.set_viewport_size({"width": 390, "height": 844})
            expect(graph.locator(".edge-label-above")).to_have_count(0)
            batch = graph.get_by_text("batch", exact=True).bounding_box()
            processed = graph.get_by_text("processed", exact=True).bounding_box()
            assert batch is not None and processed is not None
            self.assertGreaterEqual(
                max(batch["y"], processed["y"]),
                min(batch["y"] + batch["height"], processed["y"] + processed["height"]),
            )

    def test_edge_preserves_multiple_bindings_and_distinguishes_run_input(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "flow.py").write_text("""\
from workspace_sdk import Flow, Input, Output, Step

def source():
    return 1

def combine(first, second, initial):
    return first + second + initial

flow = Flow(
    inputs=("initial",),
    steps=(
        Step("Source", source, {}),
        Step("Combine", combine, {
            "first": Output("Source"), "second": Output("Source"), "initial": Input("initial"),
        }),
    ),
    output=Output("Combine"),
)
""")
            with local_ui(root, single_flow=True) as (url, _), browser_page() as page:
                page.goto(url)
                page.get_by_role("button", name="Inspect Combine", exact=True).click()
                expect(page.locator(".edge-label")).to_have_text("first, second")
                inputs = page.locator("#inspector-inputs")
                expect(inputs.get_by_role("button", name="Source", exact=True)).to_have_count(2)
                expect(inputs.get_by_text("Run input · initial")).to_be_visible()
