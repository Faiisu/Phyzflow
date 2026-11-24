import json
from math import exp
import os
from pathlib import Path
from shutil import copy2, copytree
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui, show_panel


PROJECT = Path(__file__).resolve().parents[1]


class WebUITests(unittest.TestCase):
    def test_wheel_zoom_uses_configured_speed_and_smoothing(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            for filename in ("flow.py", "sample.json", "pyproject.toml"):
                copy2(PROJECT / filename, root)
            copytree(PROJECT / "ticket_demo", root / "ticket_demo")
            for speed, duration in ((2, 0), (1, 400)):
                with self.subTest(speed=speed, duration=duration):
                    (root / "setting.json").write_text(json.dumps({"zoom": {"speed": speed, "smooth_ms": duration}}))
                    with local_ui(root, single_flow=True) as (url, _):
                        page.goto(url)
                        expect(page.get_by_role("button", name="Zoom in")).to_be_enabled()
                        predict = page.get_by_role("button", name="Inspect Predict")
                        before = predict.bounding_box()
                        assert before is not None
                        page.evaluate("""() => {
                            document.querySelector(".graph-scroll").addEventListener("wheel", () => {
                                window.zoomDuringWheel = document.querySelector("#zoom-level").textContent;
                            }, { once: true });
                        }""")
                        x, y = 550, 610
                        page.mouse.move(x, y)
                        page.mouse.wheel(0, -100)
                        scale = exp(speed * 0.2)
                        expected = f"{round(100 * scale)}%"
                        expect(page.locator("#zoom-level")).to_have_text(expected)
                        immediate = f"{round(100 * exp(speed * 0.1))}%" if duration else expected
                        self.assertEqual(page.evaluate("window.zoomDuringWheel"), immediate)
                        page.wait_for_function(
                            'scale => Math.abs(Number(document.querySelector("#graph").style.transform.slice(6, -1)) - scale) < 1e-5',
                            arg=scale,
                            timeout=3000,
                        )
                        after = predict.bounding_box()
                        assert after is not None
                        self.assertAlmostEqual(after["width"], before["width"] * scale, delta=1)
                        self.assertAlmostEqual(after["x"], x + (before["x"] - x) * scale, delta=2)
                        self.assertAlmostEqual(after["y"], y + (before["y"] - y) * scale, delta=2)
                        page.mouse.wheel(0, 100)
                        expect(page.locator("#zoom-level")).to_have_text("100%")
                        if duration:
                            page.wait_for_function(
                                'Math.abs(Number(document.querySelector("#graph").style.transform.slice(6, -1)) - 1) < 1e-5',
                                timeout=3000,
                            )
                            scales = page.evaluate("""({ x, y }) => {
                                const canvas = document.querySelector(".graph-scroll");
                                const graph = canvas.querySelector("#graph");
                                return [-100, -100, 50].map(deltaY => {
                                    canvas.dispatchEvent(new WheelEvent("wheel", {
                                        deltaY, clientX: x, clientY: y, cancelable: true,
                                    }));
                                    return Number(graph.style.transform.slice(6, -1));
                                });
                            }""", {"x": x, "y": y})
                            first, second, reversed = scales
                            self.assertLess(1, first)
                            self.assertLess(first, second)
                            self.assertLess(reversed, second)
                            expect(page.locator("#zoom-level")).to_have_text(f"{round(100 * second * exp(-0.1))}%")

    def test_graph_pans_behind_independent_overlays(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            fixture = PROJECT / "evidence-demo"
            for filename in ("flow.py", "sample.json"):
                copy2(fixture / filename, root)

            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                expect(page.get_by_role("button", name="Run flow")).to_be_enabled()
                graph = page.get_by_role("region", name="Flow dependencies")
                self.assertEqual(graph.bounding_box(), {"x": 0, "y": 0, "width": 1440, "height": 900})
                predict = page.get_by_role("button", name="Inspect Predict")
                before = predict.bounding_box()
                assert before is not None
                outline = page.get_by_role("region", name="Flow outline")
                outline_position = outline.bounding_box()
                page.mouse.move(430, 650)
                page.mouse.down()
                page.mouse.move(550, 560)
                page.mouse.up()
                after = predict.bounding_box()
                assert after is not None
                self.assertAlmostEqual(after["x"] - before["x"], 120, delta=1)
                self.assertAlmostEqual(after["y"] - before["y"], -90, delta=1)
                self.assertEqual(outline.bounding_box(), outline_position)

                inspector = page.get_by_role("region", name="Node inspector")
                show_panel(page, "Run")
                run = page.get_by_role("region", name="Run", exact=True)
                expect(inspector).to_be_visible()
                expect(run).to_be_visible()
                inspector.get_by_role("button", name="Close Inspector").click()
                expect(inspector).not_to_be_visible()
                expect(run).to_be_visible()
                run.get_by_role("button", name="Run with this input").click()
                expect(run.locator("#result pre")).to_contain_text('"billing"')
                self.assertEqual(predict.bounding_box(), after)
                page.get_by_role("button", name="Center", exact=True).click()
                self.assertEqual(predict.bounding_box(), before)

    def test_graph_stays_still_at_100_percent_with_classic_scrollbars(self):
        with TemporaryDirectory() as temp, browser_page(show_scrollbars=True) as page:
            root = Path(temp).resolve()
            for filename in ("flow.py", "sample.json", "pyproject.toml"):
                copy2(PROJECT / filename, root)
            copytree(PROJECT / "ticket_demo", root / "ticket_demo")

            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                expect(page.get_by_role("button", name="Zoom in")).to_be_enabled()
                page.add_style_tag(content="""
                    html { zoom: .9; }
                    .graph-scroll { width: 710.51px; height: 335.25px; scrollbar-width: auto; }
                    .graph-scroll::-webkit-scrollbar { display: block; width: 15px; height: 15px; }
                """)
                positions = page.evaluate("""async () => {
                    document.querySelector("#zoom-in").click();
                    document.querySelector("#zoom-reset").click();
                    const node = document.querySelector(".node");
                    const positions = [];
                    for (let frame = 0; frame < 10; frame++) {
                        await new Promise(requestAnimationFrame);
                        const {x, y} = node.getBoundingClientRect();
                        positions.push({x, y});
                    }
                    return positions;
                }""")
                expect(page.locator("#zoom-level")).to_have_text("100%")
                self.assertEqual(positions, [positions[0]] * len(positions))

    def test_graph_zoom_scales_nodes_and_edges_around_the_view_center(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            for filename in ("flow.py", "sample.json", "pyproject.toml"):
                copy2(PROJECT / filename, root)
            copytree(PROJECT / "ticket_demo", root / "ticket_demo")

            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                zoom_in = page.get_by_role("button", name="Zoom in")
                zoom_out = page.get_by_role("button", name="Zoom out")
                reset = page.get_by_role("button", name="Reset zoom to 100%")
                predict = page.get_by_role("button", name="Inspect Predict")
                expect(zoom_in).to_be_enabled()
                bounds = predict.bounding_box()
                edges = page.locator("#graph canvas").bounding_box()
                assert bounds is not None and edges is not None

                zoom_in.click()
                expect(reset).to_have_text("125%")
                enlarged = predict.bounding_box()
                enlarged_edges = page.locator("#graph canvas").bounding_box()
                assert enlarged is not None and enlarged_edges is not None
                self.assertAlmostEqual(enlarged["width"], bounds["width"] * 1.25, delta=1)
                self.assertAlmostEqual(enlarged_edges["width"], edges["width"] * 1.25, delta=2)
                self.assertAlmostEqual(
                    enlarged["x"] + enlarged["width"] / 2,
                    bounds["x"] + bounds["width"] / 2,
                    delta=2,
                )
                predict.click()
                expect(predict).to_have_attribute("aria-pressed", "true")

                for _ in range(3):
                    zoom_in.click()
                expect(reset).to_have_text("200%")
                expect(zoom_in).to_be_disabled()
                reset.click()
                expect(reset).to_have_text("100%")
                zoom_out.click()
                zoom_out.click()
                expect(reset).to_have_text("50%")
                expect(zoom_out).to_be_disabled()
                reduced = predict.bounding_box()
                assert reduced is not None
                self.assertAlmostEqual(reduced["width"], bounds["width"] / 2, delta=1)

                page.set_viewport_size({"width": 390, "height": 844})
                reset.click()
                zoom_in.click()
                expect(reset).to_have_text("125%")
                self.assertTrue(page.evaluate("document.documentElement.scrollWidth === innerWidth"))


if __name__ == "__main__":
    unittest.main()
