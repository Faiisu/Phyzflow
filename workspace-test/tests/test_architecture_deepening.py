import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from workspace_sdk import Flow, Output, Step
from workspace_sdk.deploy import DeploySettings as DeployReexport
from workspace_sdk.deploy import load_deploy_settings as load_deploy_reexport
from workspace_sdk.node_sources import SourceChanged
from workspace_sdk.project import read_project_settings as read_settings_reexport
from workspace_sdk.sheets import (
    archive_sheet_directory,
    can_delete_sheet,
    create_sheet,
    discover_flows,
    edit_sheet_flow_source,
    source_stamp,
)
from workspace_sdk.ui_settings import (
    DeploySettings,
    load_deploy_settings,
    load_ui_settings,
    read_project_settings,
)
from workspace_sdk.web import FLOW_VIEW_ASSETS, STATIC, is_flow_view_route, serve
from workspace_sdk.workspace_ui import VIEW_PATH


class FlowViewRoutingAndTransportTests(unittest.TestCase):
    def test_flow_view_routes_match_proxy_view_pattern(self):
        get_paths = [
            "/",
            "/app.css",
            "/app.js",
            "/canvas-layout.js",
            "/dashboard-ui.js",
            "/edge-routing.js",
            "/prism-syntax.js",
            "/project",
            "/dashboard-panels/workflow",
            "/dashboard-panels/0",
            "/dashboard-assets/workflow",
            "/dashboard-assets/workflow/panel1-2d-graph",
            "/dashboard-assets/3/3d_model.v2",
            "/dashboard-settings/workflow",
            "/dashboard-settings/2",
            "/node-files/0",
            "/history",
            f"/history/{'a' * 32}",
        ]
        for subpath in get_paths:
            with self.subTest(method="GET", subpath=subpath):
                self.assertTrue(is_flow_view_route("GET", subpath))
                self.assertIsNotNone(VIEW_PATH.fullmatch(f"/flows/0/view{subpath}"))

        post_paths = ["/run", "/node-source/0", "/dashboard-settings/workflow", "/dashboard-settings/1"]
        for subpath in post_paths:
            with self.subTest(method="POST", subpath=subpath):
                self.assertTrue(is_flow_view_route("POST", subpath))
                self.assertIsNotNone(VIEW_PATH.fullmatch(f"/flows/0/revision/2/view{subpath}"))

        self.assertFalse(is_flow_view_route("GET", "/run"))
        self.assertFalse(is_flow_view_route("GET", "/node-source/0"))
        self.assertFalse(is_flow_view_route("POST", "/project"))
        self.assertFalse(is_flow_view_route("POST", "/history"))
        self.assertFalse(is_flow_view_route("GET", "/unknown"))
        self.assertIsNone(VIEW_PATH.fullmatch("/flows/0/view/unknown"))

    def test_flow_server_serves_canvas_layout_and_enforces_http_transport(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            panel = root / "dashboards" / "workflow" / "panels" / "2d-graph"
            panel.mkdir(parents=True)
            (panel / "index.html").write_text("<h1>2D Graph</h1>", encoding="utf-8")
            flow = Flow((), (Step("Start", lambda: {"ok": True}, {}),), Output("Start"))
            ready_url: list[str] = []

            thread = Thread(
                target=serve,
                args=(flow, root, 0, {}),
                kwargs={"zoom": {"speed": 1.0, "smooth_ms": 160}, "on_ready": ready_url.append},
                daemon=True,
            )
            thread.start()
            while not ready_url:
                thread.join(timeout=0.05)
            base_url = ready_url[0]

            self.assertIn("/canvas-layout.js", FLOW_VIEW_ASSETS)
            with urlopen(f"{base_url}/canvas-layout.js", timeout=2) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("WORKSPACE_STEP = 20", body)
                self.assertIn("DEFAULT_PANEL_WIDTH = 600", body)
                self.assertIn("DEFAULT_PANEL_HEIGHT = 400", body)

            with urlopen(f"{base_url}/dashboard-panels/workflow", timeout=2) as response:
                panels = json.loads(response.read().decode("utf-8"))["panels"]
                self.assertEqual(panels, [{"id": "2d-graph", "label": "2d graph"}])

            with urlopen(f"{base_url}/dashboard-assets/workflow/2d-graph", timeout=2) as response:
                assets = json.loads(response.read().decode("utf-8"))
                self.assertEqual(assets["index.html"], "<h1>2D Graph</h1>")

            bad_origin = Request(
                f"{base_url}/run",
                data=b"{}",
                headers={"Content-Type": "application/json", "Origin": "http://example.invalid"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as exc:
                urlopen(bad_origin, timeout=2)
            self.assertEqual(exc.exception.code, 403)


class CanvasLayoutSeamTests(unittest.TestCase):
    def test_dashboard_ui_delegates_geometry_across_narrow_seam(self):
        dashboard_ui = (STATIC / "dashboard-ui.js").read_text(encoding="utf-8")
        canvas_layout = (STATIC / "canvas-layout.js").read_text(encoding="utf-8")
        index_html = (STATIC / "index.html").read_text(encoding="utf-8")
        app_css = (STATIC / "app.css").read_text(encoding="utf-8")
        self.assertIn("releaseBoard", dashboard_ui)
        self.assertNotIn("style.left", dashboard_ui)
        self.assertNotIn("style.width", dashboard_ui)
        self.assertIn("export function createCanvasLayout", canvas_layout)
        self.assertIn("snapStep", canvas_layout)
        self.assertIn("resolveVisualizeCollisions", canvas_layout)
        self.assertIn("enter-visualize-mode", index_html)
        self.assertIn("exit-visualize-mode", index_html)
        self.assertIn("body.is-visualize-mode", app_css)


class SheetLifecycleDeepeningTests(unittest.TestCase):
    def test_sheet_discovery_stamping_editing_and_archiving(self):
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            (workspace / "flow.py").write_text("x = 1\n", encoding="utf-8")
            (workspace / "__pycache__").mkdir()
            (workspace / "__pycache__" / "ignored.py").write_text("x = 2\n", encoding="utf-8")

            entry = create_sheet(workspace, "Signal Lab")
            sheet_root = entry.parent
            self.assertEqual([name for name, _ in discover_flows(workspace)], ["Main", "Signal Lab"])
            self.assertFalse(can_delete_sheet(workspace, workspace))
            self.assertTrue(can_delete_sheet(workspace, sheet_root))

            main_stamp_before = source_stamp(workspace, main=True)
            (sheet_root / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
            # Main stamp must ignore sheets under flows/.
            self.assertEqual(source_stamp(workspace, main=True), main_stamp_before)

            sheet_stamp_before = source_stamp(sheet_root)
            (sheet_root / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
            self.assertNotEqual(source_stamp(sheet_root), sheet_stamp_before)

            read_result = edit_sheet_flow_source(sheet_root, None, None)
            self.assertIn("from workspace_sdk import Flow", read_result["source"])
            saved = edit_sheet_flow_source(sheet_root, read_result["source"] + "# edited\n", read_result["revision"])
            self.assertNotEqual(saved["revision"], read_result["revision"])
            with self.assertRaises(SourceChanged):
                edit_sheet_flow_source(sheet_root, "# stale\n", read_result["revision"])

            archived: list[Path] = []
            archive_sheet_directory(workspace, sheet_root, lambda dst: archived.append(dst))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].parent.parent, workspace / ".workspace" / "deleted-flows")
            self.assertEqual(archived[0].name, "Signal Lab")


class ProjectSettingsConsolidationTests(unittest.TestCase):
    def test_settings_module_is_single_source_for_ui_and_deploy(self):
        self.assertIs(read_settings_reexport, read_project_settings)
        self.assertIs(load_deploy_reexport, load_deploy_settings)
        self.assertIs(DeployReexport, DeploySettings)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "setting.json").write_text(json.dumps({
                "ipv4": "0.0.0.0",
                "port": 19000,
                "zoom": {"speed": 2.0, "smooth_ms": 100},
                "deploy": {"ipv4": "127.0.0.1", "port": 8088, "container_port": 8001, "context": "local"},
            }), encoding="utf-8")
            ui = load_ui_settings(root, None)
            dep = load_deploy_settings(root)
            self.assertEqual((ui.ipv4, ui.port, ui.zoom), ("0.0.0.0", 19000, {"speed": 2.0, "smooth_ms": 100}))
            self.assertEqual((dep.ipv4, dep.port, dep.container_port, dep.context), ("127.0.0.1", 8088, 8001, "local"))

            (root / "setting.json").write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                read_project_settings(root)
