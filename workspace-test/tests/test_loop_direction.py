import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from anyio.from_thread import start_blocking_portal
from playwright.sync_api import expect

from browser_support import browser_page, local_ui
from test_application import application
from test_application_edit_locks import wait_for
from test_application_mcp import mcp_client, output
from test_application_mcp_commands import call_tool, create_two_workspaces
from workspace_sdk import Flow, Input, LoopOutput, NodeSetting, Output, Step, apply_node_settings, run
from workspace_sdk.history import RunHistory
from workspace_sdk.node_settings import LiveSettings
from workspace_sdk.project import ValidationError, flow_loops, ordered_steps
from workspace_sdk.sheets import declared_graph


LOOP_FLOW_SOURCE = """\
from workspace_sdk import Flow, Input, LoopOutput, Output, Step


def prepare(target):
    return {"target": target}


def draft(config, feedback):
    attempt = 1 if feedback is None else feedback["attempt"] + 1
    return {"attempt": attempt, "target": config["target"]}


def review(candidate):
    approved = candidate["attempt"] >= candidate["target"]
    return {"attempt": candidate["attempt"], "approved": approved}


def publish(final):
    return {"published_attempt": final["attempt"], "approved": final["approved"]}


flow = Flow(
    inputs=("target",),
    steps=(
        Step("Prepare", prepare, {"target": Input("target")}),
        Step("Draft", draft, {
            "config": Output("Prepare"),
            "feedback": LoopOutput("Review", initial=None, until=lambda res: res["approved"], max_iterations=5),
        }),
        Step("Review", review, {"candidate": Output("Draft")}),
        Step("Publish", publish, {"final": Output("Review")}),
    ),
    output=Output("Publish"),
)
"""


SELF_LOOP_SOURCE = """\
from workspace_sdk import Flow, LoopOutput, Output, Step


def counter(state):
    return state + 1


flow = Flow(
    inputs=(),
    steps=(
        Step("Counter", counter, {"state": LoopOutput("Counter", initial=0, until=lambda v: v >= 3)}),
    ),
    output=Output("Counter"),
)
"""


class LoopValidationAndExecutionTests(unittest.TestCase):
    def test_validates_loop_edges_and_rejects_invalid_loop_declarations(self):
        def fn(x):
            return x

        valid_self = Flow((), (Step("Loop", fn, {"x": LoopOutput("Loop", initial=0, max_iterations=2)}),), Output("Loop"))
        self.assertEqual([step.id for step in ordered_steps(valid_self)], ["Loop"])
        self.assertEqual(flow_loops(valid_self), [{"from": "Loop", "to": "Loop", "argument": "x", "max_iterations": 2}])

        bad_cases = (
            (
                "forward_cycle",
                Flow((), (Step("A", fn, {"x": Output("B")}), Step("B", fn, {"x": Output("A")})), Output("B")),
                "Dependency cycle:",
            ),
            (
                "unknown_loop_target",
                Flow((), (Step("A", fn, {"x": LoopOutput("Missing", initial=0, max_iterations=2)}),), Output("A")),
                "unknown step 'Missing'",
            ),
            (
                "non_cyclic_loop",
                Flow((), (Step("A", lambda: 1, {}), Step("B", fn, {"x": LoopOutput("A", initial=0, max_iterations=2)})), Output("B")),
                "does not form a cycle",
            ),
            (
                "missing_stop_condition",
                Flow((), (Step("A", fn, {"x": LoopOutput("A", initial=0)}),), Output("A")),
                "must specify 'until' or 'max_iterations'",
            ),
            (
                "invalid_max_iterations",
                Flow((), (Step("A", fn, {"x": LoopOutput("A", initial=0, max_iterations=0)}),), Output("A")),
                "positive integer",
            ),
            (
                "boolean_max_iterations",
                Flow((), (Step("A", fn, {"x": LoopOutput("A", initial=0, max_iterations=True)}),), Output("A")),
                "positive integer",
            ),
            (
                "non_callable_until",
                Flow((), (Step("A", fn, {"x": LoopOutput("A", initial=0, until="done")}),), Output("A")),
                "must be callable",
            ),
            (
                "tracked_generator_loop_source",
                Flow((), (Step("A", lambda: (yield 1), {}, track_generator=True), Step("B", fn, {"x": LoopOutput("A", initial=0, max_iterations=2)})), Output("B")),
                "tracked generator",
            ),
        )
        for label, bad_flow, message in bad_cases:
            with self.subTest(case=label):
                with self.assertRaises(ValidationError) as ctx:
                    ordered_steps(bad_flow)
                self.assertIn(message, str(ctx.exception))

    def test_declared_graph_parses_loop_dependencies_for_recovery(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "flow.py").write_text(LOOP_FLOW_SOURCE, encoding="utf-8")
            graph = declared_graph(root, {"flow.py"}, require_export=True)
            self.assertEqual([step["id"] for step in graph], ["Prepare", "Draft", "Review", "Publish"])
            draft_node = next(step for step in graph if step["id"] == "Draft")
            self.assertEqual(draft_node["dependencies"], ["Prepare"])
            self.assertEqual(draft_node["loop_dependencies"], ["Review"])

    def test_runner_executes_self_loop_multi_node_loop_and_fixed_count_loop(self):
        def inc(state):
            return state + 1

        self_loop = Flow((), (Step("Count", inc, {"state": LoopOutput("Count", initial=0, until=lambda v: v >= 3)}),), Output("Count"))
        events = []
        result = run(self_loop, {}, events.append, include_metadata=True)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.output, 3)
        self.assertEqual(
            [e["iteration"] for e in events if e.get("step_id") == "Count" and e["status"] == "succeeded"],
            [1, 2, 3],
        )

        fixed_loop = Flow((), (Step("Count", inc, {"state": LoopOutput("Count", initial=10, max_iterations=4)}),), Output("Count"))
        self.assertEqual(run(fixed_loop, {}).output, 14)

        def prepare(target):
            return {"target": target}

        def draft(config, feedback):
            attempt = 1 if feedback is None else feedback["attempt"] + 1
            return {"attempt": attempt, "target": config["target"]}

        def review(candidate):
            return {"attempt": candidate["attempt"], "approved": candidate["attempt"] >= candidate["target"]}

        def publish(final):
            return final["attempt"]

        multi = Flow(
            inputs=("target",),
            steps=(
                Step("Prepare", prepare, {"target": Input("target")}),
                Step("Draft", draft, {
                    "config": Output("Prepare"),
                    "feedback": LoopOutput("Review", initial=None, until=lambda r: r["approved"], max_iterations=5),
                }),
                Step("Review", review, {"candidate": Output("Draft")}),
                Step("Publish", publish, {"final": Output("Review")}),
            ),
            output=Output("Publish"),
        )
        multi_events = []
        multi_res = run(multi, {"target": 3}, multi_events.append, include_metadata=True)
        self.assertEqual(multi_res.status, "succeeded")
        self.assertEqual(multi_res.output, 3)

        with TemporaryDirectory() as temp:
            root = Path(temp)
            RunHistory(root).save(multi_events, {}, 12.0)
            saved = RunHistory(root).read(multi_res.id)
            assert saved is not None
            by_id = {step["id"]: step for step in saved["steps"]}
            self.assertNotIn("iterations", by_id["Prepare"])
            self.assertEqual(by_id["Draft"]["iterations"], 3)
            self.assertEqual(by_id["Review"]["iterations"], 3)
            self.assertNotIn("iterations", by_id["Publish"])

    def test_runner_supports_nested_loops_generators_and_live_settings(self):
        def outer_step(outer_acc):
            return outer_acc + 1

        def inner_step(seed, inner_acc):
            return (seed * 10 if inner_acc is None else inner_acc) + 1

        def combine(outer_val, inner_val):
            return outer_val + inner_val

        nested = Flow(
            inputs=(),
            steps=(
                Step("Outer", outer_step, {"outer_acc": LoopOutput("Combine", initial=0, max_iterations=2)}),
                Step("Inner", inner_step, {
                    "seed": Output("Outer"),
                    "inner_acc": LoopOutput("Inner", initial=None, max_iterations=2),
                }),
                Step("Combine", combine, {"outer_val": Output("Outer"), "inner_val": Output("Inner")}),
            ),
            output=Output("Combine"),
        )
        res = run(nested, {})
        self.assertEqual(res.status, "succeeded")
        self.assertEqual(res.output, 156)

        closed = []

        def gen():
            try:
                yield 1
                yield 2
            finally:
                closed.append(True)

        def take_one(values, acc):
            return acc + next(values)

        gen_loop = Flow(
            inputs=(),
            steps=(
                Step("Gen", gen, {}, track_generator=True),
                Step("Acc", take_one, {
                    "values": Output("Gen"),
                    "acc": LoopOutput("Acc", initial=0, max_iterations=2),
                }),
            ),
            output=Output("Acc"),
        )
        self.assertEqual(run(gen_loop, {}).output, 3)
        self.assertEqual(closed, [True])

        closed_per_iter = []

        def iter_gen(base):
            try:
                yield base
                yield base + 1
            finally:
                closed_per_iter.append(True)

        def head_fn(acc):
            return acc + 1

        def take_head(values):
            return next(values)

        inner_gen_loop = Flow(
            inputs=(),
            steps=(
                Step("Head", head_fn, {"acc": LoopOutput("Acc", initial=0, max_iterations=2)}),
                Step("Gen", iter_gen, {"base": Output("Head")}, track_generator=True),
                Step("Acc", take_head, {"values": Output("Gen")}),
            ),
            output=Output("Acc"),
        )
        self.assertEqual(run(inner_gen_loop, {}).output, 2)
        self.assertEqual(closed_per_iter, [True, True])

        step_setting = NodeSetting(1, minimum=1, maximum=10, live=True)
        live_settings = LiveSettings()

        def live_node(acc):
            if acc == 0:
                live_settings.queue(live_settings.run_id, "Live", "delta", 5)
            apply_node_settings()
            return acc + step_setting.get()

        live_flow = Flow(
            (),
            (Step("Live", live_node, {"acc": LoopOutput("Live", initial=0, max_iterations=2)}, settings={"delta": step_setting}),),
            Output("Live"),
        )
        self.assertEqual(run(live_flow, {}, live_settings=live_settings).output, 10)

    def test_runner_fails_loop_limit_and_skips_downstream_steps(self):
        def prepare(target):
            return {"target": target}

        def draft(config, feedback):
            attempt = 1 if feedback is None else feedback["attempt"] + 1
            return {"attempt": attempt, "target": config["target"]}

        def review(candidate):
            return {"attempt": candidate["attempt"], "approved": candidate["attempt"] >= candidate["target"]}

        def publish(final):
            return final

        flow = Flow(
            inputs=("target",),
            steps=(
                Step("Prepare", prepare, {"target": Input("target")}),
                Step("Draft", draft, {
                    "config": Output("Prepare"),
                    "feedback": LoopOutput("Review", initial=None, until=lambda r: r["approved"], max_iterations=2),
                }),
                Step("Review", review, {"candidate": Output("Draft")}),
                Step("Publish", publish, {"final": Output("Review")}),
            ),
            output=Output("Publish"),
        )
        events = []
        result = run(flow, {"target": 5}, events.append, include_metadata=True)
        self.assertEqual(result.status, "failed")
        failed = next(e for e in events if e.get("step_id") == "Draft" and e["status"] == "failed")
        self.assertEqual(failed["error_type"], "LoopLimitError")
        skipped = next(e for e in events if e.get("step_id") == "Publish")
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(skipped["blocked_by"], ["Draft"])

    def test_cli_check_and_run_support_loop_flow(self):
        executable = Path(sys.executable).with_name("workspace")
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pyproject.toml").write_text("[tool.workspace]\nformat_version = 1\n", encoding="utf-8")
            (root / "flow.py").write_text(LOOP_FLOW_SOURCE, encoding="utf-8")
            check = subprocess.run([str(executable), "check"], cwd=root, capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            run_res = subprocess.run([str(executable), "run", '{"target": 3}'], cwd=root, capture_output=True, text=True)
            self.assertEqual(run_res.returncode, 0, run_res.stderr)
            self.assertEqual(json.loads(run_res.stdout), {"published_attempt": 3, "approved": True})


class LoopGraphBrowserTests(unittest.TestCase):
    def test_loop_edges_render_directionally_without_card_intersections_and_run_in_ui(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            (root / "pyproject.toml").write_text("[tool.workspace]\nformat_version = 1\n", encoding="utf-8")
            (root / "flow.py").write_text(LOOP_FLOW_SOURCE, encoding="utf-8")
            (root / "sample.json").write_text('{"input": {"target": 3}}', encoding="utf-8")

            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                graph = page.get_by_role("region", name="Flow dependencies")
                inspector = page.get_by_role("region", name="Node inspector")
                expect(page.locator("#graph-summary")).to_have_text("4 nodes / 4 edges (1 loop)")

                draft = graph.get_by_role("button", name="Inspect Draft", exact=True)
                review = graph.get_by_role("button", name="Inspect Review", exact=True)
                draft.click()
                expect(graph.locator('.edge-label[data-loop="true"]')).to_have_text("feedback")
                expect(graph.locator('.edge-label[data-loop="true"]')).to_have_attribute("data-direction", "in")
                expect(inspector.locator("#inspector-inputs")).to_contain_text("Loop · max 5")

                canvas = graph.locator("canvas")
                intersections = canvas.evaluate("""async canvas => {
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
                self.assertEqual(intersections, [])

                loop_lane_painted = canvas.evaluate("""async canvas => {
                    await new Promise(requestAnimationFrame);
                    const ctx = canvas.getContext("2d");
                    const scale = canvas.width / canvas.offsetWidth;
                    const cards = [...canvas.parentElement.querySelectorAll(".node")];
                    const draftCard = cards.find(c => c.querySelector("h3").textContent === "Draft");
                    const reviewCard = cards.find(c => c.querySelector("h3").textContent === "Review");
                    const x = (draftCard.offsetLeft + draftCard.offsetWidth + 10 - canvas.offsetLeft) * scale;
                    const y = (Math.max(draftCard.offsetTop + draftCard.offsetHeight, reviewCard.offsetTop + reviewCard.offsetHeight) + 18 - canvas.offsetTop) * scale;
                    const w = Math.max(4, (reviewCard.offsetLeft - draftCard.offsetLeft - draftCard.offsetWidth - 20) * scale);
                    const pixels = ctx.getImageData(x, y, w, 16 * scale).data;
                    return pixels.some((value, index) => index % 4 === 3 && value > 0);
                }""")
                self.assertTrue(loop_lane_painted)

                inspector.locator("#inspector-inputs").get_by_role("button", name="Review", exact=True).click()
                expect(review).to_have_attribute("aria-pressed", "true")
                expect(graph.locator('.edge-label[data-loop="true"]')).to_have_attribute("data-direction", "out")
                expect(inspector.locator("#inspector-feeds")).to_contain_text("feedback · Loop")

                inspector.get_by_role("tab", name="Execution").click()
                page.get_by_role("button", name="Run flow", exact=True).click()
                expect(page.get_by_role("status")).to_have_text("Succeeded")
                expect(inspector.locator("#execution-iterations")).to_have_text("3")
                expect(review.locator("..").locator(".node-iterations")).to_have_text("3×")
                expect(graph.locator(".node").first.locator(".node-iterations")).to_be_hidden()

    def test_self_loop_renders_without_card_intersections(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            (root / "pyproject.toml").write_text("[tool.workspace]\nformat_version = 1\n", encoding="utf-8")
            (root / "flow.py").write_text(SELF_LOOP_SOURCE, encoding="utf-8")
            (root / "sample.json").write_text('{"input": {}}', encoding="utf-8")

            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                graph = page.get_by_role("region", name="Flow dependencies")
                expect(page.locator("#graph-summary")).to_have_text("1 node / 1 edge (1 loop)")
                expect(graph.locator('.edge-label[data-loop="true"]')).to_have_text("state")
                intersections = graph.locator("canvas").evaluate("""async canvas => {
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
                self.assertEqual(intersections, [])


class LoopMcpTests(unittest.TestCase):
    def test_validate_run_and_inspect_loop_flow_through_mcp(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            state = parent / "state"
            with application(state) as url:
                allowed_id, denied_id, token = create_two_workspaces(page, url, state, parent)
                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    opened = output(call_tool(portal, client, "open_sheet", workspace_id=allowed_id, sheet="Main"))
                    session_id = opened["session_id"]
                    flow_rev = output(call_tool(portal, client, "read_file", workspace_id=allowed_id, path="flow.py", sheet="Main"))["revision"]
                    write_res = call_tool(
                        portal, client, "write_file",
                        workspace_id=allowed_id, path="flow.py", revision=flow_rev,
                        source=LOOP_FLOW_SOURCE, sheet="Main", session_id=session_id,
                    )
                    self.assertFalse(write_res.is_error, write_res.content[0].text)

                    checked = output(call_tool(
                        portal, client, "validate_flow",
                        workspace_id=allowed_id, sheet="Main", session_id=session_id,
                    ))
                    self.assertTrue(checked["valid"], checked)
                    self.assertEqual(checked["steps"], ["Prepare", "Draft", "Review", "Publish"])
                    self.assertEqual(checked["loops"], [{
                        "from": "Review",
                        "to": "Draft",
                        "argument": "feedback",
                        "max_iterations": 5,
                    }])

                    self.assertFalse(call_tool(
                        portal, client, "close_sheet",
                        workspace_id=allowed_id, sheet="Main", session_id=session_id,
                    ).is_error)

                    denied = call_tool(portal, client, "validate_flow", workspace_id=denied_id, sheet="Main")
                    self.assertTrue(denied.is_error)
                    self.assertIn("Workspace access denied", denied.content[0].text)

                    started = output(call_tool(
                        portal, client, "run_flow",
                        workspace_id=allowed_id, sheet="Main", input={"target": 3},
                    ))
                    run_id = started["run_id"]
                    wait_for(lambda: output(call_tool(
                        portal, client, "inspect_execution",
                        workspace_id=allowed_id, sheet="Main", run_id=run_id,
                    ))["status"] == "succeeded")

                    inspected = output(call_tool(
                        portal, client, "inspect_execution",
                        workspace_id=allowed_id, sheet="Main", run_id=run_id,
                    ))
                    self.assertEqual(inspected["active_run"]["result"]["output"], {
                        "published_attempt": 3,
                        "approved": True,
                    })
                    hist_steps = {s["id"]: s for s in inspected["history"]["steps"]}
                    self.assertEqual(hist_steps["Draft"]["iterations"], 3)
                    self.assertEqual(hist_steps["Review"]["iterations"], 3)

                    failed_run = output(call_tool(
                        portal, client, "run_flow",
                        workspace_id=allowed_id, sheet="Main", input={"target": 10},
                    ))
                    failed_id = failed_run["run_id"]
                    wait_for(lambda: output(call_tool(
                        portal, client, "inspect_execution",
                        workspace_id=allowed_id, sheet="Main", run_id=failed_id,
                    ))["status"] == "failed")
                    failed_exec = output(call_tool(
                        portal, client, "inspect_execution",
                        workspace_id=allowed_id, sheet="Main", run_id=failed_id,
                    ))
                    failed_steps = {s["id"]: s for s in failed_exec["history"]["steps"]}
                    self.assertEqual(failed_steps["Draft"]["status"], "failed")
                    self.assertEqual(failed_steps["Draft"]["error_type"], "LoopLimitError")
                    self.assertEqual(failed_steps["Publish"]["status"], "skipped")
                    self.assertEqual(failed_steps["Publish"]["blocked_by"], ["Draft"])


if __name__ == "__main__":
    unittest.main()
