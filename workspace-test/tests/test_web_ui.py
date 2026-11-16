import json
from math import exp
import os
from pathlib import Path
import py_compile
from shutil import copy2, copytree
from tempfile import TemporaryDirectory
from time import monotonic, sleep
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
                outline = page.get_by_role("complementary", name="Flow outline")
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
                show_panel(page, "History")
                run = page.get_by_role("region", name="Run", exact=True)
                history = page.get_by_role("region", name="Run history")
                expect(inspector).to_be_visible()
                expect(run).to_be_visible()
                expect(history).to_be_visible()
                inspector.get_by_role("button", name="Close Inspector").click()
                expect(inspector).not_to_be_visible()
                expect(run).to_be_visible()
                expect(history).to_be_visible()
                page.get_by_role("button", name="Close Toolbar").click()
                run.get_by_role("button", name="Run with this input").click()
                expect(run.locator("#result pre")).to_contain_text('"billing"')
                expect(history).to_be_visible()
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

    def test_history_explains_an_incorrect_successful_result(self):
        fixture = PROJECT / "evidence-demo"
        sample = json.loads((fixture / "sample.json").read_text())
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            for filename in ("flow.py", "sample.json"):
                copy2(fixture / filename, root)

            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                page.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("status")).to_have_text("Succeeded")
                actual = json.loads(page.locator("#result pre").inner_text())
                self.assertNotEqual(actual, sample["output"])
                self.assertEqual(actual, {"category": "billing", "priority": "standard"})

                show_panel(page, "History")
                history = page.get_by_role("region", name="Run history")
                history.get_by_role("button", name="Refresh history").click()
                history.get_by_text("Succeeded", exact=True).click()
                predict = history.locator(".history-node").filter(
                    has=page.get_by_role("heading", name="Predict · Succeeded")
                )
                expect(predict.get_by_text("The demo predictor assigns every ticket to billing.")).to_be_visible()
                expect(predict.get_by_text("Input sample", exact=True)).to_be_visible()
                expect(predict.get_by_text("Output sample", exact=True)).to_be_visible()
                expect(predict.locator("pre").filter(has_text='"the app crashes with a blank screen"')).to_be_visible()
                expect(predict.locator("pre").filter(has_text='"billing"')).to_be_visible()
                for name in ("Prepare", "Decide"):
                    other = history.locator(".history-node").filter(
                        has=page.get_by_role("heading", name=f"{name} · Succeeded")
                    )
                    expect(other.get_by_text("Input", exact=True)).to_be_visible()
                    expect(other.get_by_text("Output", exact=True)).to_be_visible()
                    expect(other.get_by_text("sample", exact=False)).to_have_count(0)
                    expect(other).not_to_contain_text("assigns every ticket")

    def test_history_recovers_disconnected_run_and_keeps_loaded_source_version(self):
        secret = "request-value-8d708"
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            source = root / "node.py"
            source.write_text(
                "import time\n"
                "from pathlib import Path\n"
                "import torch\n"
                "from helper import fail\n"
                "def prepare(text):\n"
                "    return torch.nn.Parameter(torch.zeros((2, 3)))\n"
                "def predict(text, prepared):\n"
                "    while not Path('finish').exists():\n"
                "        time.sleep(0.01)\n"
                "    try:\n"
                "        fail(text)\n"
                "    except RuntimeError as error:\n"
                "        raise type(text, (RuntimeError,), {})(text) from error\n"
            )
            (root / "helper.py").write_text(
                "def fail(text):\n"
                "    exec(compile('raise RuntimeError(text)', text, 'exec'), {'text': text})\n"
            )
            (root / "flow.py").write_text(
                "from node import prepare, predict\n"
                "from workspace_sdk import Flow, Input, Output, Step\n"
                "flow = Flow(('text',), (\n"
                "    Step('Prepare', prepare, {'text': Input('text')}),\n"
                "    Step('Predict', predict, {'text': Input('text'), 'prepared': Output('Prepare')}),\n"
                "), Output('Predict'))\n"
            )

            with local_ui(root, single_flow=True) as (url, _):
                # The running UI has already imported the original node.
                source.write_text("\n" + source.read_text().split("    try:\n")[0] + "    return text\n")
                page.goto(url)
                button = page.get_by_role("button", name="Run flow")
                expect(button).to_be_enabled()
                show_panel(page, "Run")
                page.locator("#input").fill(json.dumps({"text": secret}))
                button.click()
                predict = page.locator(".node").filter(has=page.get_by_role("heading", name="Predict", exact=True))
                expect(predict.get_by_text("Running", exact=True)).to_be_visible()
                browser = page.context.browser
                assert browser is not None
                page.close()
                (root / "finish").touch()
                reopened = browser.new_page()
                reopened.goto(url)
                show_panel(reopened, "History")
                history = reopened.get_by_role("region", name="Run history")
                expect(history).to_be_visible()
                deadline = monotonic() + 5
                while not reopened.request.get(f"{url}/history").json() and monotonic() < deadline:
                    sleep(0.05)
                history.get_by_role("button", name="Refresh history").click()
                expect(history.get_by_text("Failed", exact=True)).to_be_visible(timeout=5000)
                history.get_by_text("Failed", exact=True).click()
                expect(history.get_by_role("heading", name="Predict · Failed")).to_be_visible()
                expect(history.get_by_text("RuntimeError", exact=True)).to_be_visible()
                expect(history.get_by_text("node.py:13", exact=True)).to_be_visible()
                expect(history.get_by_text("helper.py:2", exact=True)).to_be_visible()
                prepare = history.locator(".history-node").filter(has_text="Prepare · Succeeded")
                self.assertEqual(json.loads(prepare.locator("pre").last.inner_text()), {
                    "type": "tensor", "shape": [2, 3], "dtype": "torch.float32", "device": "cpu",
                })
                expect(history).not_to_contain_text(secret)
                expect(history.get_by_text("Duration", exact=False)).to_be_visible()
                version = history.locator(".source-version").inner_text()

            with local_ui(root, single_flow=True) as (url, _):
                reopened.goto(url)
                button = reopened.get_by_role("button", name="Run flow")
                expect(button).to_be_enabled()
                show_panel(reopened, "Run")
                reopened.locator("#input").fill(json.dumps({"text": secret}))
                button.click()
                expect(reopened.locator("#result pre")).to_contain_text(secret)
                show_panel(reopened, "History")
                history = reopened.get_by_role("region", name="Run history")
                history.get_by_role("button", name="Refresh history").click()
                expect(history.get_by_text("Failed", exact=True)).to_be_visible()
                history.get_by_text("Succeeded", exact=True).click()
                expect(history.locator(".source-version")).not_to_have_text(version)
                history.get_by_text("Failed", exact=True).click()
                expect(history.locator(".source-version")).to_have_text(version)
                expect(history.get_by_text("node.py:13", exact=True)).to_be_visible()
            records = list((root / ".workspace" / "runs").glob("*.json"))
            self.assertEqual(len(records), 2)
            for record in records:
                self.assertNotIn(secret, record.read_text())

    def test_run_diagnose_and_restart_with_edited_source(self):
        sample = json.loads((PROJECT / "sample.json").read_text())
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            for filename in ("flow.py", "sample.json", "pyproject.toml"):
                copy2(PROJECT / filename, root)
            copytree(PROJECT / "ticket_demo", root / "ticket_demo")
            source = root / "ticket_demo" / "prediction.py"
            source.write_text(
                "import time\n\n" + source.read_text().replace(
                    "    if text", "    time.sleep(0.75)\n    if text"
                )
            )
            py_compile.compile(str(source), doraise=True)

            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                button = page.get_by_role("button", name="Run flow")
                expect(button).to_be_enabled()
                expect(page.get_by_text("Depends on Prepare, Predict", exact=True)).to_have_count(1)
                expect(page.get_by_text("Depends on Prepare", exact=True)).to_have_count(1)
                inspector = page.get_by_role("region", name="Node inspector")
                expect(inspector.get_by_text("ticket_demo/entrypoint.py:4", exact=True)).to_be_visible()
                outline = page.get_by_role("complementary", name="Flow outline")
                outline.get_by_role("button", name="02 Predict").click()
                expect(page.get_by_role("button", name="Inspect Predict")).to_have_attribute("aria-pressed", "true")
                select_decide = page.get_by_role("button", name="Inspect Decide")
                select_decide.focus()
                select_decide.press("Enter")
                expect(select_decide).to_have_attribute("aria-pressed", "true")
                expect(outline.get_by_role("button", name="03 Decide")).to_have_attribute("aria-pressed", "true")
                expect(inspector.get_by_text("category", exact=False)).to_contain_text("Predict")
                expect(inspector.get_by_text("Run output", exact=True)).to_be_visible()
                page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=url)
                inspector.get_by_role("button", name="Copy location").click()
                expect(inspector.get_by_role("button", name="Location copied")).to_be_visible()
                self.assertEqual(
                    page.evaluate("navigator.clipboard.readText()"),
                    f"{root / 'ticket_demo' / 'entrypoint.py'}:12",
                )
                show_panel(page, "Run")
                expect(page.locator("#input")).to_have_value(json.dumps(sample["input"], indent=2))

                button.click()
                predict = page.locator(".node").filter(has=page.get_by_role("heading", name="Predict", exact=True))
                expect(predict.get_by_text("Running", exact=True)).to_be_visible()
                expect(page.get_by_role("status")).to_have_text("Succeeded")
                self.assertEqual(json.loads(page.locator("#result pre").inner_text()), sample["output"])
                expect(button).to_be_enabled()

                page.locator("#input").fill(json.dumps(sample["failure_input"]))
                page.get_by_role("button", name="Reset input").click()
                expect(page.locator("#input")).to_have_value(json.dumps(sample["input"], indent=2))
                page.locator("#input").fill(json.dumps(sample["failure_input"]))
                button.click()
                expect(page.get_by_role("status")).to_have_text("Failed")
                expect(page.get_by_role("heading", name="Predict failed")).to_be_visible()
                expect(page.get_by_text("RuntimeError", exact=True)).to_be_visible()
                expect(page.locator("#result .error p")).to_have_text("ticket_demo/prediction.py:6")
                expect(page.locator("#result pre")).to_contain_text("The demo predictor failed.")
                decide = page.locator(".node").filter(has=page.get_by_role("heading", name="Decide", exact=True))
                expect(decide.get_by_text("Skipped", exact=True)).to_be_visible()
                screenshot = os.environ.get("WORKSPACE_TEST_SCREENSHOT")
                if screenshot:
                    page.screenshot(path=screenshot)
                page.set_viewport_size({"width": 390, "height": 844})
                self.assertTrue(page.evaluate("document.documentElement.scrollWidth === innerWidth"))
                self.assertTrue(page.locator("#graph").evaluate(
                    """graph => {
                      const nodes = [...graph.querySelectorAll(".node")];
                      return nodes.every((node, index) =>
                        index === 0 || node.offsetTop > nodes[index - 1].offsetTop);
                    }"""
                ))
                if screenshot:
                    page.screenshot(path=screenshot.replace(".png", "-mobile.png"), full_page=True)

            original = source.stat()
            source.write_text(source.read_text().replace('"billing"', '"support"'))
            os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                page.get_by_role("button", name="Run flow").click()
                expect(page.locator("#result pre")).to_contain_text('"support"')


if __name__ == "__main__":
    unittest.main()
