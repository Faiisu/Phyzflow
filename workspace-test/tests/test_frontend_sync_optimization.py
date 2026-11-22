import gzip
from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect

from browser_support import browser_page, local_ui
from workspace_sdk.sheets import create_sheet, source_stamp
from workspace_sdk.workspace_ui.flow_sheet import FlowSheet


FLOW_SOURCE = '''from pathlib import Path
from time import sleep

from workspace_sdk import Flow, Output, Step, publish_metrics


def work():
    root = Path(__file__).parent
    tick = 0
    while not (root / "finish").exists() and tick < 400:
        tick += 1
        publish_metrics("Sample", {"Ticks": tick})
        sleep(0.05)
    return {"ticks": tick}


flow = Flow((), (Step("Work", work, {}),), Output("Work"))
'''

SIMPLE_FLOW = '''from workspace_sdk import Flow, Output, Step


def ping():
    return {"ok": True}


flow = Flow((), (Step("Ping", ping, {}),), Output("Ping"))
'''


def http_get(url: str, headers: dict[str, str] | None = None):
    parsed = urlsplit(url)
    connection = HTTPConnection(parsed.netloc, timeout=5)
    try:
        target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        connection.request("GET", target, headers=headers or {})
        with connection.getresponse() as response:
            body = response.read()
            return response.status, dict(response.getheaders()), body
    finally:
        connection.close()


def http_post(url: str, payload: dict | None = None):
    parsed = urlsplit(url)
    connection = HTTPConnection(parsed.netloc, timeout=10)
    try:
        headers = {"Origin": f"http://{parsed.netloc}"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        connection.request("POST", parsed.path, body=body, headers=headers)
        with connection.getresponse() as response:
            raw = response.read()
            return response.status, dict(response.getheaders()), raw
    finally:
        connection.close()


class FrontendSyncOptimizationTests(unittest.TestCase):
    def test_conditional_etag_gzip_and_incremental_live_run(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "flow.py").write_text(SIMPLE_FLOW, encoding="utf-8")
            with local_ui(root) as (url, _):
                status, headers, raw = http_get(f"{url}/workspace.js", {"Accept-Encoding": "gzip"})
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("Content-Encoding"), "gzip")
                self.assertEqual(headers.get("Cache-Control"), "no-cache")
                asset_etag = headers.get("ETag")
                self.assertTrue(asset_etag)
                self.assertIn(b"createLiveSync", gzip.decompress(raw))

                status_304, _, empty_body = http_get(
                    f"{url}/workspace.js",
                    {"Accept-Encoding": "gzip", "If-None-Match": asset_etag},
                )
                self.assertEqual(status_304, 304)
                self.assertEqual(empty_body, b"")

                ws_status, ws_headers, ws_body = http_get(f"{url}/workspace", {"Accept-Encoding": "gzip"})
                self.assertEqual(ws_status, 200)
                ws_etag = ws_headers.get("ETag")
                self.assertTrue(ws_etag)
                ws_payload = json.loads(
                    gzip.decompress(ws_body) if ws_headers.get("Content-Encoding") == "gzip" else ws_body
                )
                self.assertEqual(len(ws_payload["flows"]), 1)

                ws_304, _, ws_empty = http_get(f"{url}/workspace", {"If-None-Match": ws_etag})
                self.assertEqual(ws_304, 304)
                self.assertEqual(ws_empty, b"")

                open_status, _, _ = http_post(f"{url}/flows/0/open")
                self.assertEqual(open_status, 200)

                run_status, _, _ = http_post(f"{url}/flows/0/view/run", {})
                self.assertEqual(run_status, 200)

                full_status, full_headers, full_body = http_get(f"{url}/flows/0/view/live-run")
                self.assertEqual(full_status, 200)
                full_etag = full_headers.get("ETag")
                self.assertTrue(full_etag)
                full_data = json.loads(full_body)
                self.assertFalse(full_data["running"])
                self.assertTrue(full_data["events"])
                run_id = full_data["run_id"]
                cursor = full_data["cursor"]
                self.assertTrue(run_id)
                self.assertGreater(cursor, 0)

                not_mod_status, _, not_mod_body = http_get(
                    f"{url}/flows/0/view/live-run?after={cursor}&run_id={run_id}",
                    {"If-None-Match": full_etag},
                )
                self.assertEqual(not_mod_status, 304)
                self.assertEqual(not_mod_body, b"")

                delta_status, _, delta_body = http_get(
                    f"{url}/flows/0/view/live-run?after={cursor}&run_id={run_id}",
                )
                self.assertEqual(delta_status, 200)
                delta_data = json.loads(delta_body)
                self.assertTrue(delta_data["incremental"])
                self.assertEqual(delta_data["events"], [])
                self.assertEqual(delta_data["cursor"], cursor)

                partial_status, _, partial_body = http_get(
                    f"{url}/flows/0/view/live-run?after={cursor - 1}&run_id={run_id}",
                )
                self.assertEqual(partial_status, 200)
                partial_data = json.loads(partial_body)
                self.assertTrue(partial_data["incremental"])
                self.assertEqual(len(partial_data["events"]), 1)
                self.assertEqual(partial_data["events"][0][0], cursor)

                bad_status, _, _ = http_get(f"{url}/flows/0/view/live-run?after=abc")
                self.assertEqual(bad_status, 400)

                run2_status, _, _ = http_post(f"{url}/flows/0/view/run", {})
                self.assertEqual(run2_status, 200)

                reset_status, _, reset_body = http_get(
                    f"{url}/flows/0/view/live-run?after={cursor}&run_id={run_id}",
                    {"If-None-Match": full_etag},
                )
                self.assertEqual(reset_status, 200)
                reset_data = json.loads(reset_body)
                self.assertFalse(reset_data["incremental"])
                self.assertNotEqual(reset_data["run_id"], run_id)
                self.assertTrue(reset_data["events"])

    def test_batched_workspace_dashboard_endpoint_and_error_isolation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "flow.py").write_text(SIMPLE_FLOW, encoding="utf-8")
            panels_dir = root / "dashboards" / "workspace" / "panels"
            for name in ("alpha", "beta"):
                panel = panels_dir / name
                panel.mkdir(parents=True)
                (panel / "index.html").write_text(f"<h1>{name}</h1><output></output>", encoding="utf-8")
                (panel / "bridge.json").write_text(
                    json.dumps({
                        "versions": [1],
                        "sources": [{"id": "status", "sheet": ".", "mode": "latest", "field": "status"}],
                    }),
                    encoding="utf-8",
                )
                (panel / "dashboard.js").write_text(
                    'workspaceDashboard.subscribe(({sources}) => {\n'
                    '  document.querySelector("output").textContent = sources[0].state;\n'
                    '});\n',
                    encoding="utf-8",
                )
            with local_ui(root) as (url, _):
                status, headers, raw = http_get(
                    f"{url}/workspace-dashboard/data?panels=alpha,beta,missing-panel",
                    {"Accept-Encoding": "gzip"},
                )
                self.assertEqual(status, 200)
                etag = headers.get("ETag")
                self.assertTrue(etag)
                payload = json.loads(
                    gzip.decompress(raw) if headers.get("Content-Encoding") == "gzip" else raw
                )
                self.assertEqual(payload["scope"], "workspace")
                self.assertFalse(payload["live_active"])
                self.assertEqual(set(payload["panels"]), {"alpha", "beta"})
                self.assertIn("missing-panel", payload["errors"])

                status_304, _, empty = http_get(
                    f"{url}/workspace-dashboard/data?panels=alpha,beta,missing-panel",
                    {"If-None-Match": etag},
                )
                self.assertEqual(status_304, 304)
                self.assertEqual(empty, b"")

    def test_inactive_sheets_pause_polling_and_dashboard_uses_batched_feed(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve()
            (root / "flow.py").write_text(SIMPLE_FLOW, encoding="utf-8")
            second = create_sheet(root, "Second").parent
            (second / "flow.py").write_text(FLOW_SOURCE, encoding="utf-8")
            for name in ("alpha", "beta"):
                panel = second / "dashboards" / "workflow" if name == "alpha" else second / "dashboards" / "workflow" / "panels" / name
                panel.mkdir(parents=True, exist_ok=True)
                (panel / "index.html").write_text(f"<h1>{name}</h1><output>idle</output>", encoding="utf-8")
                (panel / "bridge.json").write_text(
                    json.dumps({"versions": [1]}),
                    encoding="utf-8",
                )
                (panel / "dashboard.js").write_text(
                    'workspaceDashboard.subscribe(({run, liveMetrics}) => {\n'
                    '  const val = liveMetrics?.Work?.Sample?.Ticks ?? run?.steps?.find(s => s.id === "Work")?.metrics?.Sample?.Ticks;\n'
                    '  document.querySelector("output").textContent = String(val ?? "idle");\n'
                    '});\n',
                    encoding="utf-8",
                )

            with local_ui(root) as (url, _):
                for panel_id in ("default", "beta"):
                    post_status, _, _ = http_post(
                        f"{url}/dashboard/placements",
                        {"action": "add", "sheet": "flows/Second", "scope": None, "id": panel_id},
                    )
                    self.assertEqual(post_status, 200)
                responses = []
                page.on("response", lambda resp: responses.append((resp.url, resp.status)))
                page.goto(url)
                first_view = page.frame_locator('iframe[title="Main flow"]')
                expect(first_view.get_by_role("button", name="Inspect Ping")).to_be_visible()

                page.get_by_role("tab", name="Second").click()
                second_view = page.frame_locator('iframe[title="Second flow"]')
                expect(second_view.get_by_role("button", name="Inspect Work")).to_be_visible()

                page.wait_for_timeout(400)
                responses.clear()
                page.wait_for_timeout(2200)

                sheet0_polls = [entry for entry in responses if "/flows/0/" in entry[0] and "/live-run" in entry[0]]
                sheet1_polls = [entry for entry in responses if "/flows/1/" in entry[0] and "/live-run" in entry[0]]
                ws_304s = [entry for entry in responses if entry[0].endswith("/workspace") and entry[1] == 304]
                self.assertEqual(sheet0_polls, [])
                self.assertTrue(sheet1_polls)
                self.assertTrue(any(status == 304 for _, status in sheet1_polls))
                self.assertTrue(ws_304s)

                responses.clear()
                page.get_by_role("button", name="Dashboard", exact=True).click()
                dialog = page.get_by_role("dialog", name="Workspace dashboard")
                board = dialog.frame_locator('iframe[title="Workspace Dashboard"]')
                expect(board.frame_locator('iframe[title="Second · Workflow · Overview"]').locator("output")).to_have_text("idle")
                expect(board.frame_locator('iframe[title="Second · Workflow · beta"]').locator("output")).to_have_text("idle")

                page.wait_for_timeout(300)
                responses.clear()
                page.wait_for_timeout(2300)

                any_sheet_polls = [entry for entry in responses if "/live-run" in entry[0]]
                placement_polls = [entry for entry in responses if "/dashboard/placement-assets/" in entry[0] and entry[0].endswith("/data")]
                self.assertEqual(any_sheet_polls, [])
                self.assertTrue(placement_polls)

                responses.clear()
                board.get_by_role("button", name="Back to sheets").click()
                expect(dialog).to_be_hidden()

                page.wait_for_timeout(500)
                resumed_sheet1 = [entry for entry in responses if "/flows/1/" in entry[0] and "/live-run" in entry[0]]
                self.assertTrue(resumed_sheet1)

    def test_backend_workspace_caching_avoids_redundant_walks_and_worker_probes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "flow.py").write_text(SIMPLE_FLOW, encoding="utf-8")
            nodes = root / "nodes" / "ping"
            nodes.mkdir(parents=True)
            helper = nodes / "helper.py"
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            sheet = FlowSheet(
                "Main",
                root,
                {"speed": 1.0, "smooth_ms": 0},
                workspace_root=root,
                main=True,
            )
            try:
                sheet.open()
                baseline = sheet.attempted_source
                self.assertEqual(baseline, source_stamp(root, main=True))
                with patch("os.walk", side_effect=AssertionError("os.walk should not run when directories are unchanged")):
                    self.assertIsNone(sheet.changed_source("ready"))
                    helper.write_text("VALUE = 222\n", encoding="utf-8")
                    updated = sheet.changed_source("ready")
                self.assertIsNotNone(updated)
                self.assertEqual(updated, source_stamp(root, main=True))
                (nodes / "extra.py").write_text("EXTRA = True\n", encoding="utf-8")
                added = sheet.changed_source("ready")
                self.assertIsNotNone(added)
                self.assertNotEqual(added, updated)
                self.assertEqual(added, source_stamp(root, main=True))
                with patch("workspace_sdk.workspace_ui.flow_sheet.build_opener", side_effect=AssertionError("worker /status HTTP probe should be cached while idle")):
                    self.assertEqual(sheet.status(), "ready")
                self.assertEqual(sheet.worker_status(probe=True), "ready")
            finally:
                sheet.stop()


if __name__ == "__main__":
    unittest.main()
