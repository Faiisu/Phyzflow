import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from zipfile import ZipFile

from anyio.from_thread import start_blocking_portal
from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application, sign_in
from test_application_edit_locks import issue_token, wait_for
from test_application_mcp import mcp_client, output


def call_tool(portal, client, tool_name, /, **arguments):
    return portal.call(client.call_tool, tool_name, arguments)


EXPECTED_TOOLS = {
    "list_workspaces",
    "inspect_workspace",
    "create_sheet",
    "delete_sheet",
    "inspect_dashboards",
    "open_sheet",
    "heartbeat_sheet",
    "stage_draft",
    "close_sheet",
    "list_files",
    "read_file",
    "search_code",
    "write_file",
    "apply_patch",
    "read_node_settings",
    "update_node_setting",
    "validate_flow",
    "run_flow",
    "run_all_flows",
    "stop_flow",
    "inspect_execution",
    "read_console_logs",
    "configure_console_logs",
    "install_dependencies",
    "list_workspace_files",
    "stat_workspace_file",
    "read_workspace_text",
    "write_workspace_text",
    "move_workspace_file",
    "delete_workspace_file",
    "begin_workspace_transfer",
    "write_workspace_chunk",
    "read_workspace_chunk",
    "finish_workspace_transfer",
    "cancel_workspace_transfer",
}

FORBIDDEN_TOOLS = {"run_command", "update_workspace", "restart_workspace"}


def create_two_workspaces(page, url: str, state: Path, parent: Path) -> tuple[str, str, str]:
    sign_in(page, url, state)
    allowed_root = parent / "AllowedWorkspace"
    denied_root = parent / "DeniedWorkspace"
    ids = []
    for root in (allowed_root, denied_root):
        response = page.request.post(
            f"{url}/workspaces", headers={"Origin": url}, data={"path": str(root)},
        )
        if response.status != 201:
            raise AssertionError(f"Could not create workspace: {response.status} {response.text()}")
        ids.append(response.json()["id"])
    token = issue_token(page, url, "Flow Developer", [ids[0]])
    return ids[0], ids[1], token


def build_wheel(directory: Path, package_name: str, module_name: str, body: str) -> Path:
    wheel = directory / f"{module_name}-1.0.0-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(f"{module_name}.py", body)
        archive.writestr(
            f"{module_name}-1.0.0.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {package_name}\nVersion: 1.0.0\n",
        )
        archive.writestr(
            f"{module_name}-1.0.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{module_name}-1.0.0.dist-info/RECORD", "")
    return wheel


class ApplicationMcpCommandsTests(unittest.TestCase):
    def test_installed_app_protects_checkout_when_started_outside_it(self):
        checkout = Path(__file__).resolve().parents[2]
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            state = parent / "state"
            with application(state, cwd=parent) as url:
                sign_in(page, url, state)
                for protected in (checkout, checkout / "docs"):
                    with self.subTest(protected=protected):
                        response = page.request.post(
                            f"{url}/workspaces",
                            headers={"Origin": url},
                            data={"path": str(protected), "action": "add"},
                        )
                        self.assertEqual(response.status, 400, response.text())
                        self.assertIn("the Phyzflow source checkout", response.json()["error"])

    def test_mcp_tool_catalog_and_workspace_grant_enforcement(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            state = parent / "state"
            with application(state) as url:
                allowed_id, denied_id, token = create_two_workspaces(page, url, state, parent)

                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    tools = {tool.name for tool in portal.call(client.list_tools).tools}
                    self.assertTrue(EXPECTED_TOOLS.issubset(tools), f"Missing tools: {sorted(EXPECTED_TOOLS - tools)}")
                    self.assertTrue(FORBIDDEN_TOOLS.isdisjoint(tools), f"Unexpected forbidden tools: {sorted(FORBIDDEN_TOOLS & tools)}")

                    for forbidden_name, args in (
                        ("run_command", {"workspace_id": allowed_id, "program": "python", "argv": ["-V"]}),
                        ("update_workspace", {"workspace_id": allowed_id}),
                        ("restart_workspace", {"workspace_id": allowed_id}),
                    ):
                        result = portal.call(client.call_tool, forbidden_name, args)
                        self.assertTrue(result.is_error, f"{forbidden_name} should fail")

                    for tool_name, args in (
                        ("inspect_workspace", {"workspace_id": denied_id}),
                        ("create_sheet", {"workspace_id": denied_id, "name": "Extra"}),
                        ("delete_sheet", {"workspace_id": denied_id, "sheet": "Main"}),
                        ("inspect_dashboards", {"workspace_id": denied_id}),
                        ("open_sheet", {"workspace_id": denied_id, "sheet": "Main", "mode": "edit"}),
                        ("list_files", {"workspace_id": denied_id}),
                        ("read_file", {"workspace_id": denied_id, "path": "flow.py"}),
                        ("search_code", {"workspace_id": denied_id, "query": "Flow"}),
                        ("write_file", {"workspace_id": denied_id, "path": "flow.py", "revision": "missing", "source": "x"}),
                        ("apply_patch", {"workspace_id": denied_id, "path": "flow.py", "revision": "abc", "old_text": "a", "new_text": "b"}),
                        ("read_node_settings", {"workspace_id": denied_id}),
                        ("update_node_setting", {"workspace_id": denied_id, "node_id": "Start", "name": "x", "value": 1}),
                        ("validate_flow", {"workspace_id": denied_id}),
                        ("run_flow", {"workspace_id": denied_id}),
                        ("run_all_flows", {"workspace_id": denied_id}),
                        ("stop_flow", {"workspace_id": denied_id}),
                        ("inspect_execution", {"workspace_id": denied_id}),
                        ("read_console_logs", {"workspace_id": denied_id}),
                        ("configure_console_logs", {"workspace_id": denied_id, "save_logs": False}),
                        ("install_dependencies", {"workspace_id": denied_id, "packages": ["pytest"]}),
                    ):
                        denied = portal.call(client.call_tool, tool_name, args)
                        self.assertTrue(denied.is_error, f"{tool_name} should deny ungranted workspace")
                        self.assertIn("Workspace access denied", denied.content[0].text)

    def test_mcp_sheet_management_files_search_and_boundary_protection(self):
        repo_root = Path(__file__).resolve().parents[2]
        spec_file = repo_root / "docs" / "application-spec.md"
        self.assertTrue(spec_file.is_file())

        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            state = parent / "state"
            workspace_root = parent / "FlowLab"
            with application(state) as url:
                sign_in(page, url, state)

                for protected_path in (repo_root, repo_root / "docs", state):
                    rejected = page.request.post(
                        f"{url}/workspaces",
                        headers={"Origin": url},
                        data={"path": str(protected_path), "action": "add"},
                    )
                    self.assertEqual(rejected.status, 400, f"{protected_path} must not be registrable")

                created_ws = page.request.post(
                    f"{url}/workspaces",
                    headers={"Origin": url},
                    data={"path": str(workspace_root)},
                )
                self.assertEqual(created_ws.status, 201)
                workspace_id = created_ws.json()["id"]
                token_a = issue_token(page, url, "Developer A", [workspace_id])
                token_b = issue_token(page, url, "Developer B", [workspace_id])

                (workspace_root / "spec-link.md").symlink_to(spec_file)
                (workspace_root / "state-link").symlink_to(state, target_is_directory=True)
                state_wheel = build_wheel(state, "state-evil", "state_evil", "VALUE = 'bad'\n")
                (workspace_root / "state-wheel.whl").symlink_to(state_wheel)

                with (
                    portal.wrap_async_context_manager(mcp_client(url, token_a)) as client_a,
                    portal.wrap_async_context_manager(mcp_client(url, token_b)) as client_b,
                ):
                    created_sheet = call_tool(portal, client_a, "create_sheet", workspace_id=workspace_id, name="Analysis")
                    self.assertFalse(created_sheet.is_error, created_sheet.content[0].text)
                    self.assertEqual(output(created_sheet)["name"], "Analysis")
                    self.assertEqual(output(created_sheet)["entrypoint"], "flows/Analysis/flow.py")

                    duplicate_sheet = call_tool(portal, client_a, "create_sheet", workspace_id=workspace_id, name="analysis")
                    self.assertTrue(duplicate_sheet.is_error)

                    sheets = output(call_tool(portal, client_a, "inspect_workspace", workspace_id=workspace_id))["sheets"]
                    self.assertEqual(
                        [entry["name"] for entry in sheets],
                        ["Main", "Analysis"],
                    )

                    listed_main = output(call_tool(portal, client_a, "list_files", workspace_id=workspace_id, path=".", sheet="Main"))
                    main_paths = {entry["path"] for entry in listed_main["entries"]}
                    self.assertIn("flow.py", main_paths)
                    self.assertIn("nodes", main_paths)
                    self.assertNotIn(".venv", main_paths)
                    self.assertNotIn(".workspace", main_paths)
                    self.assertNotIn("spec-link.md", main_paths)
                    self.assertNotIn("state-link", main_paths)

                    listed_analysis = output(call_tool(portal, client_a, "list_files", workspace_id=workspace_id, path="nodes/start", sheet="Analysis"))
                    self.assertEqual(
                        [entry["path"] for entry in listed_analysis["entries"]],
                        ["nodes/start/__init__.py", "nodes/start/entrypoint.py", "nodes/start/settings.json"],
                    )

                    search_res = output(call_tool(portal, client_a, "search_code", workspace_id=workspace_id, query="Hello from Workspace", sheet="Analysis"))
                    self.assertEqual(len(search_res["matches"]), 1)
                    self.assertEqual(search_res["matches"][0]["path"], "nodes/start/entrypoint.py")

                    opened = output(call_tool(portal, client_a, "open_sheet", workspace_id=workspace_id, sheet="Analysis", mode="edit"))
                    session_a = opened["session_id"]

                    analysis_root = workspace_root / "flows" / "Analysis"
                    (analysis_root / "spec-link.md").symlink_to(spec_file)
                    (analysis_root / "state-link").symlink_to(state, target_is_directory=True)

                    locked_delete = call_tool(portal, client_b, "delete_sheet", workspace_id=workspace_id, sheet="Analysis")
                    self.assertTrue(locked_delete.is_error)
                    self.assertIn("Developer A", locked_delete.content[0].text)

                    delete_main = call_tool(portal, client_a, "delete_sheet", workspace_id=workspace_id, sheet="Main")
                    self.assertTrue(delete_main.is_error)

                    relative_to_spec = os.path.relpath(spec_file, workspace_root)
                    for blocked_path in (
                        relative_to_spec,
                        str(spec_file),
                        "../../docs/application-spec.md",
                        "spec-link.md",
                        "state-link/hijack.py",
                        ".venv/pyvenv.cfg",
                        ".workspace/edit-lock.json",
                    ):
                        self.assertTrue(call_tool(portal, client_a, "read_file", workspace_id=workspace_id, path=blocked_path, sheet="Main").is_error)
                        self.assertTrue(call_tool(
                            portal, client_a, "write_file",
                            workspace_id=workspace_id, path=blocked_path, revision="missing", source="hijack", sheet="Analysis", session_id=session_a,
                        ).is_error, f"write_file should reject {blocked_path}")

                    for bad_pkg in (
                        ["-e", str(repo_root / "workspace")],
                        ["--target=/tmp/escape", "pytest"],
                        ["--index-url=https://invalid.example", "pytest"],
                        [str(repo_root / "workspace")],
                        [str(spec_file)],
                        [str(state_wheel)],
                        ["state-wheel.whl"],
                        [f"workspace-sdk @ file://{repo_root / 'workspace'}"],
                        ["."],
                        ["flow.py"],
                        [],
                    ):
                        denied_install = call_tool(portal, client_a, "install_dependencies", workspace_id=workspace_id, packages=bad_pkg)
                        self.assertTrue(denied_install.is_error, f"install_dependencies must reject {bad_pkg!r}")

                    self.assertFalse(call_tool(portal, client_a, "close_sheet", workspace_id=workspace_id, sheet="Analysis", session_id=session_a).is_error)

                    deleted = call_tool(portal, client_a, "delete_sheet", workspace_id=workspace_id, sheet="Analysis")
                    self.assertFalse(deleted.is_error, deleted.content[0].text)
                    self.assertEqual(output(deleted)["status"], "deleted")
                    self.assertFalse((workspace_root / "flows" / "Analysis").exists())
                    self.assertTrue(any((workspace_root / ".workspace" / "deleted-flows").glob("*/Analysis")))
                    remaining = output(call_tool(portal, client_a, "inspect_workspace", workspace_id=workspace_id))["sheets"]
                    self.assertEqual([entry["name"] for entry in remaining], ["Main"])

    def test_mcp_full_flow_development_lifecycle_and_ui_parity(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            state = parent / "state"
            workspace_root = parent / "FullFlow"
            wheel_dir = parent / "wheels"
            wheel_dir.mkdir()
            wheel = build_wheel(
                wheel_dir,
                "mcp-shared-dep",
                "mcp_shared_dep",
                'TAG = "wheel-v1"\ndef compute(value, multiplier):\n    return value * multiplier\n',
            )

            with application(state) as url:
                sign_in(page, url, state)
                created_ws = page.request.post(
                    f"{url}/workspaces",
                    headers={"Origin": url},
                    data={"path": str(workspace_root)},
                )
                self.assertEqual(created_ws.status, 201)
                workspace_id = created_ws.json()["id"]
                token = issue_token(page, url, "Autonomous Author", [workspace_id])

                with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                    opened_main = output(call_tool(portal, client, "open_sheet", workspace_id=workspace_id, sheet="Main", mode="edit"))
                    session_main = opened_main["session_id"]

                    broken_flow = call_tool(
                        portal, client, "write_file",
                        workspace_id=workspace_id, path="flow.py",
                        revision=output(call_tool(portal, client, "read_file", workspace_id=workspace_id, path="flow.py", sheet="Main"))["revision"],
                        source="from workspace_sdk import Flow\nflow = (\n",
                        sheet="Main", session_id=session_main,
                    )
                    self.assertFalse(broken_flow.is_error, broken_flow.content[0].text)

                    invalid = output(call_tool(portal, client, "validate_flow", workspace_id=workspace_id, sheet="Main", session_id=session_main))
                    self.assertFalse(invalid["valid"])
                    self.assertEqual(invalid["stage"], "validation")
                    self.assertEqual(invalid["file"], "flow.py")
                    self.assertEqual(invalid["line"], 2)
                    self.assertIn("flow.py:2", invalid["error"])

                    entry_rev = output(call_tool(
                        portal, client, "read_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py", sheet="Main",
                    ))["revision"]
                    main_node_source = (
                        "from pathlib import Path\n"
                        "import sys\n"
                        "from time import monotonic, sleep\n"
                        "import mcp_shared_dep\n"
                        "from workspace_sdk import NodeSetting, apply_node_settings, publish_metrics, record_metrics\n\n"
                        "MULTIPLIER = NodeSetting(default=2, minimum=1, maximum=10, live=True)\n"
                        "SOURCE_VERSION = 'v1'\n\n"
                        "def prepare():\n"
                        "    print('prepare stdout', flush=True)\n"
                        "    print('prepare stderr', file=sys.stderr, flush=True)\n"
                        "    publish_metrics('Progress', {'Pct': 50})\n"
                        "    if Path('wait-flag').exists():\n"
                        "        Path('main-waiting').touch()\n"
                        "        deadline = monotonic() + 15\n"
                        "        while Path('wait-flag').exists() and monotonic() < deadline:\n"
                        "            sleep(0.02)\n"
                        "        apply_node_settings()\n"
                        "    record_metrics('Summary', {'Multiplier': MULTIPLIER.get()})\n"
                        "    return {'val': mcp_shared_dep.compute(3, MULTIPLIER.get()), 'ver': SOURCE_VERSION, 'exe': sys.executable}\n\n"
                        "def process(data, fail=False):\n"
                        "    if fail:\n"
                        "        raise ValueError('forced failure')\n"
                        "    return {**data, 'tag': mcp_shared_dep.TAG}\n\n"
                        "def finish(data):\n"
                        "    return data\n"
                    )
                    wrote_entry = call_tool(
                        portal, client, "write_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py",
                        revision=entry_rev, source=main_node_source,
                        sheet="Main", session_id=session_main,
                    )
                    self.assertFalse(wrote_entry.is_error, wrote_entry.content[0].text)
                    entry_rev = output(wrote_entry)["revision"]

                    main_flow_source = (
                        "from workspace_sdk import Flow, Input, Output, Step\n"
                        "from nodes.start.entrypoint import MULTIPLIER, finish, prepare, process\n\n"
                        "flow = Flow(\n"
                        "    ('fail',),\n"
                        "    (\n"
                        "        Step('Prepare', prepare, {}, settings={'multiplier': MULTIPLIER}),\n"
                        "        Step('Process', process, {'data': Output('Prepare'), 'fail': Input('fail')}),\n"
                        "        Step('Finish', finish, {'data': Output('Process')}),\n"
                        "    ),\n"
                        "    Output('Finish'),\n"
                        ")\n"
                    )
                    flow_rev = output(call_tool(portal, client, "read_file", workspace_id=workspace_id, path="flow.py", sheet="Main"))["revision"]
                    wrote_flow = call_tool(
                        portal, client, "write_file",
                        workspace_id=workspace_id, path="flow.py",
                        revision=flow_rev, source=main_flow_source,
                        sheet="Main", session_id=session_main,
                    )
                    self.assertFalse(wrote_flow.is_error, wrote_flow.content[0].text)

                    wrote_sample = call_tool(
                        portal, client, "write_file",
                        workspace_id=workspace_id, path="sample.json",
                        revision="missing", source='{"input": {"fail": false}}\n',
                        sheet="Main", session_id=session_main,
                    )
                    self.assertFalse(wrote_sample.is_error, wrote_sample.content[0].text)

                    for dash_path, dash_content in (
                        (
                            "dashboards/workflow/index.html",
                            '<main><h1>MCP Overview</h1><output id="status-text">Idle</output></main>\n',
                        ),
                        (
                            "dashboards/workflow/style.css",
                            "#status-text { color: var(--accent); }\n",
                        ),
                        (
                            "dashboards/workflow/dashboard.js",
                            'const out = document.querySelector("#status-text");\n'
                            'workspaceDashboard.subscribe(({ run }) => {\n'
                            '  out.value = run?.status ?? "idle";\n'
                            '});\n',
                        ),
                    ):
                        dash_write = call_tool(
                            portal, client, "write_file",
                            workspace_id=workspace_id, path=dash_path, revision="missing",
                            source=dash_content, sheet="Main", session_id=session_main,
                        )
                        self.assertFalse(dash_write.is_error, dash_write.content[0].text)

                    dash_info = output(call_tool(portal, client, "inspect_dashboards", workspace_id=workspace_id, sheet="Main"))
                    self.assertEqual(len(dash_info["workflow"]), 1)
                    self.assertTrue(dash_info["workflow"][0]["ready"])

                    missing_dep_check = output(call_tool(portal, client, "validate_flow", workspace_id=workspace_id, sheet="Main", session_id=session_main))
                    self.assertFalse(missing_dep_check["valid"])
                    self.assertIn("mcp_shared_dep", missing_dep_check["error"])

                    installed = call_tool(portal, client, "install_dependencies", workspace_id=workspace_id, packages=[str(wheel)])
                    self.assertFalse(installed.is_error, installed.content[0].text)
                    self.assertEqual(output(installed)["interpreter"], str(workspace_root / ".venv/bin/python"))

                    valid_check = output(call_tool(portal, client, "validate_flow", workspace_id=workspace_id, sheet="Main", session_id=session_main))
                    self.assertTrue(valid_check["valid"], valid_check)
                    self.assertEqual(valid_check["steps"], ["Prepare", "Process", "Finish"])

                    settings_before = output(call_tool(portal, client, "read_node_settings", workspace_id=workspace_id, sheet="Main"))
                    mult_meta = settings_before["settings"]["Prepare"]["multiplier"]
                    self.assertEqual(mult_meta["type"], "int")
                    self.assertEqual(mult_meta["value"], 2)
                    self.assertEqual(mult_meta["minimum"], 1)
                    self.assertEqual(mult_meta["maximum"], 10)
                    self.assertTrue(mult_meta["live"])

                    bad_setting = call_tool(
                        portal, client, "update_node_setting",
                        workspace_id=workspace_id, node_id="Prepare", name="multiplier", value=999, sheet="Main", mode="next",
                    )
                    self.assertTrue(bad_setting.is_error)

                    saved_setting = output(call_tool(
                        portal, client, "update_node_setting",
                        workspace_id=workspace_id, node_id="Prepare", name="multiplier", value=5, sheet="Main", mode="next",
                    ))
                    self.assertEqual(saved_setting["status"], "saved")
                    self.assertEqual(saved_setting["value"], 5)

                    settings_after = output(call_tool(portal, client, "read_node_settings", workspace_id=workspace_id, sheet="Main"))
                    self.assertEqual(settings_after["settings"]["Prepare"]["multiplier"]["value"], 5)

                    self.assertFalse(call_tool(portal, client, "create_sheet", workspace_id=workspace_id, name="Worker").is_error)
                    opened_worker = output(call_tool(portal, client, "open_sheet", workspace_id=workspace_id, sheet="Worker", mode="edit"))
                    session_worker = opened_worker["session_id"]
                    worker_rev = output(call_tool(
                        portal, client, "read_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py", sheet="Worker",
                    ))["revision"]
                    worker_source = (
                        "from pathlib import Path\n"
                        "import sys\n"
                        "from time import sleep\n"
                        "import mcp_shared_dep\n\n"
                        "def start():\n"
                        "    print(f'worker started {mcp_shared_dep.TAG}', flush=True)\n"
                        "    Path('worker-started').write_text(sys.executable, encoding='utf-8')\n"
                        "    while True:\n"
                        "        sleep(0.05)\n"
                    )
                    self.assertFalse(call_tool(
                        portal, client, "write_file",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py",
                        revision=worker_rev, source=worker_source,
                        sheet="Worker", session_id=session_worker,
                    ).is_error)
                    self.assertFalse(call_tool(portal, client, "close_sheet", workspace_id=workspace_id, sheet="Worker", session_id=session_worker).is_error)

                    (workspace_root / "wait-flag").touch()
                    run1 = output(call_tool(portal, client, "run_flow", workspace_id=workspace_id, sheet="Main", input={"fail": False}))
                    run1_id = run1["run_id"]
                    wait_for(lambda: (workspace_root / "main-waiting").is_file())

                    live_exec = output(call_tool(portal, client, "inspect_execution", workspace_id=workspace_id, sheet="Main", run_id=run1_id))
                    self.assertEqual(live_exec["status"], "running")
                    prepare_live = next(s for s in live_exec["active_run"]["steps"] if s["id"] == "Prepare")
                    self.assertEqual(prepare_live["status"], "running")
                    self.assertEqual(prepare_live["live_metrics"]["Progress"]["Pct"], 50)

                    live_update = output(call_tool(
                        portal, client, "update_node_setting",
                        workspace_id=workspace_id, node_id="Prepare", name="multiplier", value=7,
                        sheet="Main", mode="live", run_id=run1_id,
                    ))
                    self.assertEqual(live_update["status"], "queued")

                    patched_entry = call_tool(
                        portal, client, "apply_patch",
                        workspace_id=workspace_id, path="nodes/start/entrypoint.py",
                        revision=entry_rev, old_text="SOURCE_VERSION = 'v1'", new_text="SOURCE_VERSION = 'v2'",
                        sheet="Main", session_id=session_main,
                    )
                    self.assertFalse(patched_entry.is_error, patched_entry.content[0].text)

                    all_started = output(call_tool(portal, client, "run_all_flows", workspace_id=workspace_id))
                    self.assertIn("Worker", all_started["started"])
                    self.assertNotIn("Main", all_started["started"])
                    self.assertEqual(all_started["errors"], {})
                    wait_for(lambda: (workspace_root / "flows/Worker/worker-started").is_file())
                    self.assertEqual(
                        (workspace_root / "flows/Worker/worker-started").read_text(encoding="utf-8"),
                        str(workspace_root / ".venv/bin/python"),
                    )

                    (workspace_root / "wait-flag").unlink()
                    wait_for(lambda: output(call_tool(portal, client, "inspect_execution", workspace_id=workspace_id, sheet="Main", run_id=run1_id))["status"] == "succeeded")
                    done1 = output(call_tool(portal, client, "inspect_execution", workspace_id=workspace_id, sheet="Main", run_id=run1_id))
                    self.assertEqual(done1["active_run"]["result"]["output"], {
                        "val": 21,
                        "ver": "v1",
                        "exe": str(workspace_root / ".venv/bin/python"),
                        "tag": "wheel-v1",
                    })
                    self.assertEqual(done1["history"]["status"], "succeeded")
                    prepare_hist = next(s for s in done1["history"]["steps"] if s["id"] == "Prepare")
                    self.assertEqual(prepare_hist["metrics"]["Summary"]["Multiplier"], 7)

                    worker_logs = output(call_tool(portal, client, "read_console_logs", workspace_id=workspace_id, sheet="Worker"))
                    self.assertIn("worker started wheel-v1", worker_logs["stdout"])

                    stopped_worker = output(call_tool(portal, client, "stop_flow", workspace_id=workspace_id, sheet="Worker"))
                    self.assertEqual(stopped_worker["status"], "stopped")

                    run2 = output(call_tool(portal, client, "run_flow", workspace_id=workspace_id, sheet="Main", input={"fail": False}))
                    run2_id = run2["run_id"]
                    wait_for(lambda: output(call_tool(portal, client, "inspect_execution", workspace_id=workspace_id, sheet="Main", run_id=run2_id))["status"] == "succeeded")
                    done2 = output(call_tool(portal, client, "inspect_execution", workspace_id=workspace_id, sheet="Main", run_id=run2_id))
                    self.assertEqual(done2["active_run"]["result"]["output"]["ver"], "v2")
                    self.assertEqual(done2["active_run"]["result"]["output"]["val"], 15)
                    self.assertNotEqual(done1["history"]["source_version"], done2["history"]["source_version"])

                    run_fail = output(call_tool(portal, client, "run_flow", workspace_id=workspace_id, sheet="Main", input={"fail": True}))
                    run_fail_id = run_fail["run_id"]
                    wait_for(lambda: output(call_tool(portal, client, "inspect_execution", workspace_id=workspace_id, sheet="Main", run_id=run_fail_id))["status"] == "failed")
                    failed_exec = output(call_tool(portal, client, "inspect_execution", workspace_id=workspace_id, sheet="Main", run_id=run_fail_id))
                    self.assertEqual(failed_exec["history"]["status"], "failed")
                    process_step = next(s for s in failed_exec["history"]["steps"] if s["id"] == "Process")
                    finish_step = next(s for s in failed_exec["history"]["steps"] if s["id"] == "Finish")
                    self.assertEqual(process_step["status"], "failed")
                    self.assertEqual(process_step["error_type"], "ValueError")
                    self.assertEqual(process_step["source"]["file"], "nodes/start/entrypoint.py")
                    self.assertEqual(finish_step["status"], "skipped")
                    self.assertEqual(finish_step["blocked_by"], ["Process"])

                    default_logs = output(call_tool(portal, client, "read_console_logs", workspace_id=workspace_id, sheet="Main", run_id=run2_id))
                    self.assertEqual(default_logs["settings"], {"save_logs": True, "retention_days": 7})
                    self.assertIn("prepare stdout", default_logs["stdout"])
                    self.assertIn("prepare stderr", default_logs["stderr"])

                    disabled_cfg = output(call_tool(portal, client, "configure_console_logs", workspace_id=workspace_id, sheet="Main", save_logs=False))
                    self.assertEqual(disabled_cfg["settings"], {"save_logs": False, "retention_days": 7})

                    run_nosave = output(call_tool(portal, client, "run_flow", workspace_id=workspace_id, sheet="Main", input={"fail": False}))
                    run_nosave_id = run_nosave["run_id"]
                    wait_for(lambda: output(call_tool(portal, client, "inspect_execution", workspace_id=workspace_id, sheet="Main", run_id=run_nosave_id))["status"] == "succeeded")
                    self.assertFalse((workspace_root / ".workspace" / "logs" / run_nosave_id).exists())
                    live_only_logs = output(call_tool(portal, client, "read_console_logs", workspace_id=workspace_id, sheet="Main"))
                    self.assertEqual(live_only_logs["run_id"], run_nosave_id)
                    self.assertIn("prepare stdout", live_only_logs["stdout"])
                    self.assertIn("prepare stderr", live_only_logs["stderr"])

                    updated_cfg = output(call_tool(portal, client, "configure_console_logs", workspace_id=workspace_id, sheet="Main", save_logs=True, retention_days=3))
                    self.assertEqual(updated_cfg["settings"], {"save_logs": True, "retention_days": 3})

                    run_saved = output(call_tool(portal, client, "run_flow", workspace_id=workspace_id, sheet="Main", input={"fail": False}))
                    run_saved_id = run_saved["run_id"]
                    wait_for(lambda: output(call_tool(portal, client, "inspect_execution", workspace_id=workspace_id, sheet="Main", run_id=run_saved_id))["status"] == "succeeded")
                    self.assertTrue((workspace_root / ".workspace" / "logs" / run_saved_id).is_dir())
                    self.assertGreaterEqual(len(output(call_tool(portal, client, "inspect_execution", workspace_id=workspace_id, sheet="Main"))["recent_runs"]), 4)

                    self.assertFalse(call_tool(portal, client, "close_sheet", workspace_id=workspace_id, sheet="Main", session_id=session_main).is_error)

                    page.goto(f"{url}/workspaces/{workspace_id}/")
                    main_frame = page.frame_locator('iframe[title="Main flow"]')
                    expect(main_frame.get_by_role("button", name="Inspect Finish")).to_be_visible()
                    expect(main_frame.get_by_label("Save console logs")).to_be_checked()
                    expect(main_frame.get_by_label("Retention (days)")).to_have_value("3")
                    expect(main_frame.get_by_role("region", name="Run console").get_by_label("stdout")).to_contain_text("prepare stdout")
                    main_frame.locator('[data-vis-node=""][data-vis-panel="default"]').click()
                    panel = main_frame.frame_locator("#dashboard-preview iframe")
                    expect(panel.get_by_role("heading", name="MCP Overview")).to_be_visible()

                    opened_del = output(call_tool(portal, client, "open_sheet", workspace_id=workspace_id, sheet="Worker", mode="edit"))
                    deleted_with_lock = call_tool(
                        portal, client, "delete_sheet",
                        workspace_id=workspace_id, sheet="Worker", session_id=opened_del["session_id"],
                    )
                    self.assertFalse(deleted_with_lock.is_error, deleted_with_lock.content[0].text)
                    self.assertEqual(output(deleted_with_lock)["status"], "deleted")

                    page.goto(url)
                    page.get_by_role("button", name="MCP access").click()
                    page.get_by_label("Token", exact=True).select_option(label="Autonomous Author")
                    page.get_by_role("checkbox", name="FullFlow").uncheck()
                    page.get_by_role("button", name="Save grants").click()
                    expect(page.get_by_role("status").filter(has_text="Grants saved.")).to_be_visible()

                    for tool_name, args in (
                        ("inspect_execution", {"workspace_id": workspace_id}),
                        ("read_console_logs", {"workspace_id": workspace_id}),
                        ("run_all_flows", {"workspace_id": workspace_id}),
                        ("install_dependencies", {"workspace_id": workspace_id, "packages": [str(wheel)]}),
                    ):
                        revoked = portal.call(client.call_tool, tool_name, args)
                        self.assertTrue(revoked.is_error)
                        self.assertIn("Workspace access denied", revoked.content[0].text)
