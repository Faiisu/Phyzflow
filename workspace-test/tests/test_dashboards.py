import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from browser_support import browser_page, local_ui
from playwright.sync_api import expect


class DashboardTests(unittest.TestCase):
    def test_dashboard_bridge_compatibility_is_local_to_each_panel(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            (root / "flow.py").write_text(
                'from pathlib import Path\n'
                'from time import sleep\n'
                'from workspace_sdk import Flow, Output, Step\n'
                'def start():\n'
                '    for _ in range(400):\n'
                '        if Path(__file__).with_name("finish").exists():\n'
                '            return 1\n'
                '        sleep(0.05)\n'
                '    raise TimeoutError("The test did not release the flow.")\n'
                'flow = Flow((), (Step("Start", start, {}),), Output("Start"))\n'
            )
            workflow = root / "dashboards" / "workflow"
            panels = {"default": None, "compatible": [2, 1], "incompatible": [2, 3]}
            for panel_id, versions in panels.items():
                panel = workflow if panel_id == "default" else workflow / "panels" / panel_id
                panel.mkdir(parents=True)
                (panel / "index.html").write_text("<output></output>")
                (panel / "dashboard.js").write_text("""
                    workspaceDashboard.subscribe(snapshot => {
                      document.querySelector("output").textContent =
                        `${workspaceDashboard.version}: ${snapshot.run?.status ?? "idle"}`;
                    });
                """)
                if versions is not None:
                    (panel / "bridge.json").write_text(json.dumps({"versions": versions}))
            with (workflow / "panels" / "incompatible" / "dashboard.js").open("a") as script:
                script.write('workspaceDashboard.openPanel("default", "Start");\n')

            with local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                page.get_by_role("button", name="Run flow", exact=True).click()
                try:
                    expect(page.locator("#status")).to_have_text("Running")
                    page.get_by_role("button", name="Open all", exact=True).click()
                    compatible = page.frame_locator('iframe[title="Workflow dashboard · compatible"]')
                    undeclared = page.frame_locator('iframe[title="Workflow dashboard"]')
                    incompatible = page.get_by_role("region", name="Workflow dashboard · incompatible")
                    with self.subTest(combination=panels["incompatible"]):
                        expected = ", ".join(map(str, panels["incompatible"]))
                        expect(incompatible.get_by_role("status")).to_have_text(
                            f"Incompatible Dashboard Bridge. Observed version 1. Supported versions {expected}."
                        )
                        expect(incompatible.locator("iframe")).to_have_count(0)
                        expect(page.get_by_role("region", name="Start dashboard")).to_have_count(0)
                    for name, frame in (("compatible", compatible), ("default", undeclared)):
                        with self.subTest(combination=panels[name]):
                            expect(frame.owner).to_have_attribute("sandbox", "allow-scripts allow-forms")
                            expect(frame.locator("output")).to_have_text("1: running")
                finally:
                    (root / "finish").touch()
                for name, frame in (("compatible", compatible), ("default", undeclared)):
                    with self.subTest(combination=panels[name]):
                        expect(frame.locator("output")).to_have_text("1: succeeded")
                expect(page.get_by_role("region", name="Start dashboard")).to_have_count(0)
                expect(page.locator("#status")).to_have_text("Succeeded")

    def test_workflow_and_node_panels_keep_live_data_with_the_current_frame(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            (root / "flow.py").write_text(
                "from pathlib import Path\n"
                "from time import sleep\n"
                "from workspace_sdk import Flow, NodeSetting, Output, Step, apply_node_settings, publish_metrics\n"
                "limit = NodeSetting(2, minimum=1, maximum=8, live=True)\n"
                "def prepare():\n"
                "    publish_metrics('Progress', {'limit': 1})\n"
                "    return 1\n"
                "def work(prepared):\n"
                "    while not Path('finish').exists():\n"
                "        apply_node_settings()\n"
                "        publish_metrics('Progress', {'limit': limit.get()})\n"
                "        sleep(0.05)\n"
                "    return prepared\n"
                "flow = Flow((), (Step('Prepare', prepare, {}),\n"
                "    Step('Work', work, {'prepared': Output('Prepare')}, settings={'Limit': limit})), Output('Work'))\n"
            )
            for folder in ("workflow", "nodes/Work"):
                panel = root / "dashboards" / folder
                panel.mkdir(parents=True)
                (panel / "index.html").write_text("""
                    <output id="snapshot"></output><output id="saved"></output>
                    <output id="result">Waiting</output><output id="changes"></output>
                    <output id="initial"></output>
                    <button id="open">Open Work</button><button id="save">Save 3</button>
                    <button id="apply">Apply 4</button>
                """)
                (panel / "dashboard.js").write_text("""
                    const display = (id, text) => { document.querySelector(id).textContent = text; };
                    workspaceDashboard.subscribe(snapshot => {
                      const keys = Object.keys(snapshot.liveMetrics).sort().join(",");
                      const limit = snapshot.liveMetrics.Work?.Progress?.limit;
                      display("#snapshot", [snapshot.scope, snapshot.run?.status, keys, limit].join(" / "));
                      display("#saved", snapshot.settings.Work?.Limit?.value ?? "");
                      display("#changes", snapshot.settingChanges.map(change => change.status).join(","));
                    });
                    document.querySelector("#open").onclick = () => workspaceDashboard.openNode("Work");
                    document.querySelector("#save").onclick = () => workspaceDashboard.setSetting("Work", "Limit", 3);
                    document.querySelector("#apply").onclick = async () => {
                      const result = await workspaceDashboard.setSetting("Work", "Limit", 4, "live");
                      display("#result", result.status);
                    };
                    workspaceDashboard.setSetting("Work", "Limit", 99).then(result => {
                      display("#initial", result.status);
                    });
                    window.addEventListener("message", event => {
                      if (event.data?.type === "dashboard-setting-result" && event.data.result.status === "saved") {
                        display("#result", "Unexpected save");
                      }
                    });
                """)

            pending = {}

            def hold_initial_settings(route):
                request = route.request
                first_read = request.method == "GET" and "GET" not in pending
                delayed_save = request.method == "POST" and request.post_data_json["value"] == 3
                if first_read or delayed_save:
                    response = route.fetch()
                    pending[request.method] = (route, response)
                    return
                route.continue_()

            with local_ui(root, single_flow=True) as (url, _):
                page.route("**/dashboard-settings/workflow", hold_initial_settings)
                page.goto(url)
                page.get_by_role("button", name="Run flow", exact=True).click()
                try:
                    page.locator('[data-vis-node=""][data-vis-panel="default"]').click()
                    workflow = page.get_by_role("region", name="Workflow dashboard")
                    frame = workflow.frame_locator("iframe")
                    expect(frame.locator("#snapshot")).to_have_text("workflow / running / Prepare,Work / 2")
                    expect(frame.locator("#initial")).to_have_text("rejected")
                    with page.expect_event(
                        "requestfailed", predicate=lambda request: request.url.endswith("/dashboard-settings/workflow"),
                        timeout=5000,
                    ):
                        workflow.get_by_role("button", name="Reload dashboard").click()
                    expect(frame.locator("#snapshot")).to_have_text("workflow / running / Prepare,Work / 2")
                    expect(frame.locator("#saved")).to_have_text("2")
                    frame.get_by_role("button", name="Save 3").click()
                    frame.get_by_role("button", name="Open Work").click()

                    node = page.get_by_role("region", name="Work dashboard")
                    frame = node.frame_locator("iframe")
                    expect(frame.locator("#snapshot")).to_have_text("node / running / Work / 2")
                    expect(frame.locator("#saved")).to_have_text("3")
                    route, response = pending["POST"]
                    self.assertEqual(response.json()["status"], "saved")
                    with page.expect_response(lambda response: response.url.endswith("/dashboard-settings/1")):
                        route.fulfill(response=response)
                    expect(frame.locator("#saved")).to_have_text("3")
                    expect(frame.locator("#result")).to_have_text("Waiting")
                    frame.get_by_role("button", name="Apply 4").click()
                    expect(frame.locator("#result")).to_have_text(re.compile(r"queued|applied"))
                    expect(frame.locator("#changes")).to_have_text("applied")
                    expect(frame.locator("#snapshot")).to_have_text("node / running / Work / 4")

                    node.get_by_role("button", name="Close dashboard").click()
                    expect(node).to_have_count(0)
                    page.locator('[data-vis-node="Work"][data-vis-panel="default"]').click()
                    expect(frame.locator("#snapshot")).to_have_text("node / running / Work / 4")
                    expect(frame.locator("#result")).to_have_text("Waiting")
                finally:
                    (root / "finish").touch()
                expect(frame.locator("#snapshot")).to_have_text("node / succeeded / Work / 4")

    def test_dashboard_files_reload_and_cannot_read_workspace_or_navigate_to_network(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            project = Path(temp).resolve()
            (project / "flow.py").write_text(
                'from workspace_sdk import Flow, Output, Step\n'
                'flow = Flow((), (Step("Start", lambda: 1, {}),), Output("Start"))\n'
            )
            node = project / "dashboards" / "nodes" / "Start"
            node.mkdir(parents=True)
            (node / "index.html").write_text('<h1>Original</h1>')
            (node / "style.css").write_text("h1 { color: rgb(7, 8, 9); }")
            (node / "setting.js").write_text('document.body.dataset.settingLoaded = "true";')
            script = node / "dashboard.js"
            script.write_text("""
                document.body.dataset.settingLoadedFirst = document.body.dataset.settingLoaded;
                let isolated = false;
                try { parent.document.body; } catch { isolated = true; }
                document.body.dataset.isolated = isolated;
                fetch("https://outside.test/fetch").catch(() => {
                  document.body.dataset.blocked = true;
                });
                window.addEventListener("message", event => {
                  if (event.data === "navigate") location.href = "https://outside.test/navigate";
                });
            """)
            outside = []
            page.on("request", lambda request: outside.append(request.url) if "outside.test" in request.url else None)
            page.route("https://outside.test/**", lambda route: route.fulfill(body="Network reached"))
            with local_ui(project, single_flow=True) as (url, _):
                page.goto(url)
                page.locator('[data-vis-node="Start"][data-vis-panel="default"]').click()
                frame = page.frame_locator("#dashboard-preview iframe")
                expect(frame.locator("h1")).to_have_text("Original")
                expect(frame.locator("h1")).to_have_css("color", "rgb(7, 8, 9)")
                expect(frame.locator("body")).to_have_attribute("data-setting-loaded-first", "true")
                expect(frame.locator("body")).to_have_attribute("data-isolated", "true")
                expect(frame.locator("body")).to_have_attribute("data-blocked", "true")
                with page.expect_event("framenavigated", predicate=lambda frame: frame.url.startswith("chrome-error:")):
                    page.locator("#dashboard-preview iframe").evaluate('frame => frame.contentWindow.postMessage("navigate", "*")')
                self.assertEqual(outside, [])

                (node / "index.html").write_text("<h1>Updated</h1>")
                page.get_by_role("button", name="Reload dashboard").click()
                expect(frame.locator("h1")).to_have_text("Updated")

                script.unlink()
                script.symlink_to(project / "flow.py")
                page.get_by_role("button", name="Reload dashboard").click()
                expect(page.locator("#dashboard-message")).to_contain_text("inside the dashboard folder")
                self.assertEqual(page.request.get(f"{url}/dashboard-assets/999").status, 404)
                self.assertEqual(page.request.get(f"{url}/dashboard-assets/%2F..%2Fflow.py").status, 404)
