# Workspace development

This directory contains the Workspace SDK, Runner, CLI, Web UI, and deployment tooling. The SDK is installed as a dependency by the sample project in [`workspace-test/`](../workspace-test/).

See the [application specification](../docs/application-spec.md) for requirements and acceptance scenarios.

## Open a local Workspace on macOS

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/). On first use, uv can download Python 3.12 if it is unavailable.
2. Unzip the Workspace release for your Mac's architecture and open `Workspace.app`.
3. Choose an existing Workspace or an empty folder. The launcher uses the project's `.venv`, installs the SDK from the release, and opens the configured local URL in your browser. Select **+** to create a sheet, then **Run flow**.
4. Share the project folder path with an AI agent or open it in your IDE. If startup fails, the launcher shows the error. UI logs are in the project's `.workspace/launcher-*.log`. Edit the project's `setting.json` to resolve a port conflict.

Build a release on macOS from the repository root with `sh workspace/release/macos/build.sh`. The ZIP is written to `workspace/dist/` by default after the compatibility check passes. The build requires uv and Google Chrome, selects Python 3.12 through uv, uses the local Mac architecture, and signs the bundle ad hoc. The build uses the manifest from the latest macOS ZIP in the output directory that contains one. Set `PREVIOUS_RELEASE_MANIFEST` to use the preceding release manifest from another location.

## Sign in to the local Phyzflow application

Use the [repository launchers](../README.md#start-the-local-application) to create the application environment and start Phyzflow. If you already have the SDK environment, install MCP support and start the application from the repository root:

```sh
uv pip install './workspace[mcp]'
workspace app
```

Open the address printed by the application. Use `--state-dir` to choose a separate state directory for another installation.

On first startup Phyzflow generates a password for the built-in `admin` account. Read the password on the host from `~/.phyzflow/admin/initial-password` or from `admin/initial-password` under your selected state directory. The application account owns the directory and can read the file. Save the password securely and remove the delivery file once you have signed in. Phyzflow keeps the password verifier in the state directory and uses the same credential on subsequent starts. The browser session lasts up to 12 hours and ends when the application restarts.

Keep application state separate from Workspace folders.

On Windows, new Workspaces use uv-managed Python 3.12 so Phyzflow can stop their process trees. An existing Workspace can reuse a virtual environment backed by uv-managed or standalone Python 3.12. If its environment uses Microsoft Store Python, move the existing environment aside, add the Workspace, and reinstall its dependencies in the new environment.

Select **Network settings** on the landing page to set the application's bind IPv4 address and port. Phyzflow saves the settings in `network.json` under its state directory and reads them at startup. By default, Phyzflow listens on all IPv4 interfaces (`0.0.0.0`) on port `18080`. Save the settings and restart Phyzflow to apply them. Use `workspace app --ipv4 127.0.0.1 --port 0` to override the saved address and start on an available port for a single launch.

Use direct HTTP on loopback or an isolated test network. For HTTPS, bind Phyzflow to loopback and follow the [application deployment design](../docs/application-spec.md#deployment).

Select **MCP access** on the landing page, name the client, and select the Workspaces it can access. Copy the generated token when it appears. Configure the MCP client for Streamable HTTP at `/mcp` on the application origin and supply `Authorization: Bearer <token>`. The `list_workspaces` tool lists granted Workspaces, and `inspect_workspace` lists sheets for a Workspace ID. Select an existing token to save new grants or revoke it. Changes apply on the next request, including for clients that are already connected.

Authorized MCP clients have full flow-development parity inside their granted Workspaces across these tool categories:

- **Workspace and sheet management:** `list_workspaces`, `inspect_workspace`, `create_sheet`, `rename_sheet`, and `delete_sheet`.
- **Sheet editing sessions:** `open_sheet`, `heartbeat_sheet`, `stage_draft`, and `close_sheet`.
- **Files, source, and dashboards:** `list_files`, `read_file`, `search_code`, `write_file`, `apply_patch`, and `inspect_dashboards`.
- **Node settings:** `read_node_settings` and `update_node_setting`.
- **Validation and execution:** `validate_flow`, `run_flow`, `run_all_flows`, and `stop_flow`.
- **Inspection, logs, and dependencies:** `inspect_execution`, `read_console_logs`, `configure_console_logs`, and `install_dependencies` (targeting the granted Workspace's `.venv` only).

All MCP commands enforce live Workspace grants and keep application state, the Phyzflow source checkout, raw shell execution, and application updates or restarts outside the MCP boundary.

### Transfer and manage Workspace files

Use `list_workspace_files` with a directory path and the returned `next_after` as the next call's `after` argument. Paths use forward slashes and are relative to the Workspace root. Use `.` to list the root. `stat_workspace_file` returns a regular file's byte size and revision. Use `read_workspace_text` and `write_workspace_text` for UTF-8 files up to 128 KiB. Text writes and uploads take the current revision for replacement or `missing` for creation. Use `move_workspace_file` to rename or move a file on the same filesystem without overwriting a destination. Use `delete_workspace_file` to send a file to the host Trash or Recycle Bin. Moves and deletions require the source revision. Use Sheet commands to remove Sheets and their entrypoints.

Project dotfiles such as `.gitignore` and `.github/` are accessible. Protected paths include `.git`, `.workspace`, Python environments and caches, Phyzflow application data and source, and temporary transfer files. Symbolic links, junctions, hard-linked files, and special files are outside the file tool scope. When an affected Sheet is locked, pass its edit session in `session_ids`, keyed by the Sheet's Workspace-relative directory. Use `.` for Main and, for example, `flows/Secondary` for a secondary Sheet. A move across Sheets checks both leases.

Set `PHYZFLOW_MCP_TOKEN` in your environment, then stream a file through the installed MCP SDK client:

```sh
python -m workspace_sdk.application_mcp.file_client --url http://127.0.0.1:18080 --workspace-id WORKSPACE_ID upload ./archive.zip assets/archive.zip
python -m workspace_sdk.application_mcp.file_client --url http://127.0.0.1:18080 --workspace-id WORKSPACE_ID download assets/archive.zip ./received.zip
```

Upload is create-only by default. Use `--revision REVISION` to replace a known version. Use `--session SHEET_PATH=SESSION_ID` when the affected Sheet is locked. Downloads create a new local file and verify size and SHA-256 before publishing it.

Clients using tools directly call `begin_workspace_transfer` with `workspace_id`, `path`, and `direction` (`upload` or `download`). Uploads also provide `revision`, `size`, and a lowercase hexadecimal `sha256`. Pass the returned `transfer_id` to `write_workspace_chunk` with `offset` and Base64 `data`, or to `read_workspace_chunk` with `offset` and an optional byte `count`. Continue at the returned `next_offset`. Retry the last chunk at the same offset after a lost response. Call `finish_workspace_transfer` once all bytes have been transferred, passing `session_ids` for locked upload targets. It returns the verified digest, byte size, and revision. Repeat finalization with the same transfer ID if its response was lost. Use `cancel_workspace_transfer` to discard a pending transfer.

Each chunk carries up to 512 KiB of file bytes. The server stages the upload on the destination filesystem and reserves 256 MiB of free disk space. Available space and the filesystem determine the maximum file size. A 1 GiB file uses 2,048 chunk calls in each direction. A token can have one upload and one download active, with up to 32 active transfers per installation. Transfers expire after 15 minutes of inactivity. Restarting the server discards pending transfers. Each call checks the current Workspace grant and the server checks the destination revision again at upload commit.

To reproduce the 1 GiB round-trip check from the repository root after installing `./workspace[mcp]` and the test dependencies:

```sh
PYTHONPATH=workspace-test:workspace-test/tests PHYZFLOW_MCP_LARGE_FILE_TEST=1 \
  python -m unittest -v test_application_mcp_files.ApplicationMcpFilesTests.test_installed_gib_round_trip
```

A localhost run on macOS arm64 with Python 3.12.11 uploaded 1 GiB in 17.7 seconds and downloaded it in 15.8 seconds. The source, Workspace file, and downloaded file had the same SHA-256. Peak sampled server RSS was 110 MiB. Client peak RSS was 81 MiB uploading and 84 MiB downloading. Each side used about 1 GiB of staging space while it wrote the file.

## Work on a selected project from the CLI

Use the `workspace` executable installed in the project's Python environment. Run these commands from any directory:

```sh
workspace --project "$project_path" sheets
workspace --project "$project_path" check --sheet "First Flow"
workspace --project "$project_path" run --sheet "First Flow" '{}'
workspace --project "$project_path" ui
```

Use `workspace init "$project_path"` to initialize a new project and `workspace --project "$project_path" new-sheet "First Flow"` to create a runnable Python sheet. The CLI prints the path to its Python entry point. A browser already open on the project discovers the new sheet without a restart. After editing Python, select the sheet and click **Reload flow** to refresh the graph and use the edited code on the next run.

To change a sheet's name, select it and click **Rename sheet**. MCP clients can call `rename_sheet` with the Workspace ID, current `sheet` name, and new `name`. Include the editing session ID when the sheet is locked by your session. Renaming preserves the project folder, run history, and Workspace Dashboard sources. Use the new name in CLI and MCP calls. `Main` remains an alias for the root sheet.

In the Workspace, select a sheet and choose **Edit flow.py** to open its flow source. **Save** writes to the project file. If another editor changed the file, copy any changes you want to keep and reopen the editor to see the latest version. If **Reload flow** reports a validation error, use the diagnostic to locate it. You can reopen **Edit flow.py** to fix the flow source, save it, and retry **Reload flow**.

Open sheets reload automatically after Python files in their project folders change and the edits settle. The next run uses the reloaded code. If a run is active or the source editor has unsaved text, the sheet shows **Reload pending** and waits. If validation fails, the sheet shows the diagnostic and retries after another source change. To resume an explicitly stopped sheet, select **Open flow**.

Use `workspace init --with-main "$project_path"` to start with a runnable **Main** sheet.

For `workspace deploy`, set the host IPv4 address, host port, container port, and Docker context under `deploy` in the project's `setting.json`. See [the deployment settings and CLI overrides](../workspace-test/deployment-trial/README.md#deploy-and-verify).

## One folder per node

The **+** button and `new-sheet` create the same starter layout.

```text
First Flow/
├── flow.py
├── pyproject.toml
└── nodes/
    ├── __init__.py
    └── start/
        ├── __init__.py
        ├── entrypoint.py
        └── settings.json
```

Add one package under `nodes/` per node. Use a `snake_case` folder name, put the callable in its `entrypoint.py`, and keep node-specific helpers beside it. Import the callable in `flow.py` and wire it up with `Step`. The starter includes `nodes` in `tool.workspace.sources` so validation checks every Python file under it, including helpers the flow has yet to import. Workspace initialization also writes an agent guide with the layout.

Double-click a node on the execution graph or select **View files** in the Inspector to browse its package. The editor highlights Python and JSON syntax. Select a file and use **Save** to write changes to disk. If the file changed in another editor, reopen it before saving. Select **Reload flow** to run the saved version. Nodes declared directly in the flow directory display their source file.

Double-click a node under `nodes/` to open its `settings.json` first. The file is a JSON object whose keys match the node's `Step.settings` names. New sheets start with an empty object. For an older node, the editor shows its declared defaults and creates the file when you select **Save**. Omitted entries use the Python defaults. Workspace validates the file on save and project load. Select **Reload flow** to use the new defaults. Values saved from a dashboard take precedence for subsequent runs, and live changes apply for the current run.

Set `track_generator=True` on a step that returns a Python generator for a downstream node to iterate. The producer stays Running while the iterator is open and succeeds when the iterator is exhausted. Its duration covers the iterator's lifetime, including time between values. The Runner closes unfinished generators at the end of the run and records them as Stopped. The consumer can call `publish_output(value)` to show the latest bounded JSON preview during the run. Live previews are ephemeral.

Open a node’s **Execution** tab to see its status and timing. While the node runs, call `publish_metrics("Indexing", {"Progress (%)": 60, "Rows per second": 42})` from its Python code to update **Live progress and metrics**. Use `record_metrics` for measurements you want to keep in the completed run.

## Build a dashboard for a workflow or node

To edit dashboard files in the browser, follow the [Dashboard Editor guide](../docs/dashboard-editor.md).

Write or have an AI agent generate `index.html`, optionally with `style.css`, `setting.js`, and `dashboard.js`, in the workflow's `dashboards/workflow/` or `dashboards/nodes/<step-id>/` folder. Use the exact step ID for ordinary folder names, including spaces. Percent-encode reserved path characters and non-ASCII characters using UTF-8, and encode the entire name for `.` and `..` as `%2E` and `%2E%2E`. Workspace loads the companion files automatically and executes `setting.js` before `dashboard.js`. Use inline assets, data images, or browser APIs such as canvas. The browser policy restricts resource loads and navigation and isolates the frame from Workspace's page.

Select the panel in the **Visualize** drawer or use **Visualize** in the node inspector. Choose a saved run or **Live run**. Click **Reload dashboard** to load file changes without restarting the flow. The live view reconnects to an active run after a browser refresh, restoring node status and the latest previews, metrics, and frames. Saved runs remain available after reopening Workspace. See [Telemetry Control Room](../workspace-test/sheets-demo/flows/Telemetry%20Control%20Room/README.md) for a working example.

Drag the dashboard title bar to move it on the graph and drag the lower-right corner to resize it. Focus the title bar and use the arrow keys to move or hold **Shift** with an arrow key to resize. Select **Full screen** to expand the dashboard, then **Exit full screen** to return to its previous position and size.

In `dashboard.js`, call `window.workspaceDashboard.subscribe(render)`. The callback receives a snapshot and the call returns a function to unsubscribe.

| Field | Contents |
| --- | --- |
| `scope`, `nodeId`, `mode` | Workflow or node scope, the selected node ID or null, and live or saved mode. |
| `graph` | Nodes with `id` and `dependencies`, restricted to the dashboard scope. |
| `run` | Null before data is available, otherwise the run ID, status, timing and scoped `steps`. Each step can include status, timing, blockers, error type and completed metrics. Saved runs include `source_version`. |
| `previews`, `liveMetrics` | Latest explicit previews and numeric groups, keyed by node ID. Call `publish_output(value)` or `publish_metrics(group, values)` in the node to send these live snapshots. Use fixed metric labels and publish at a rate appropriate to the workload. Use `record_metrics` to retain a completed measurement in history. |
| `liveFrames` | Latest JPEG data URLs keyed by node ID while a run is active. Call `publish_frame(jpeg_bytes)` to share a frame up to 256 KiB. Use the data URL as an image source. Frames are cleared when the run ends and are excluded from saved runs. |
| `settings`, `settingChanges` | Editable definitions and saved values keyed by node ID and setting name, plus recent changes from the live run. |

The workflow overview can call `workspaceDashboard.openNode(nodeId)` to open a node dashboard on the same run.

## Build a dashboard from multiple sheets

Put `index.html`, `dashboard.js`, and optional `style.css` in `dashboards/workspace/` at the Workspace root. To add another panel, put its files in `dashboards/workspace/panels/<panel-id>/`. Open **Dashboard** in the Workspace toolbar to see all configured panels together on a full-window Visualize canvas. Use **Panels** to show, hide, or refresh panels and to enter browser full screen. Drag a panel by its title or resize it from its lower-right corner. Focus the title and use the arrow keys to move it or hold **Shift** to resize. The installed application saves the Workspace layout. Select **Back to sheets** to return to the flows.

Use the [Dashboard Editor guide](../docs/dashboard-editor.md) to create a starter panel, edit its source files, or remove it.

Call `inspect_dashboards` with `sheet: null` to inspect Workspace panels and their Bridge contract through MCP.

Declare the panel's inputs in `bridge.json`. Give each source a unique ID and use the sheet directory relative to the Workspace root. Use `.` for Main and `flows/<sheet-folder>` for a secondary sheet.

```json
{
  "versions": [1],
  "sources": [
    {"id": "main-duration", "sheet": ".", "field": "duration_ms"},
    {"id": "hand-duration", "sheet": "flows/Hand Detection", "field": "duration_ms"},
    {
      "id": "hand-count",
      "sheet": "flows/Hand Detection",
      "mode": "live",
      "metric": {"step": "Detect Hands", "group": "Detection", "name": "Count"}
    }
  ]
}
```

- Select one field (`status`, `started_at`, `finished_at`, or `duration_ms`) or one metric (`step`, `group`, and `name`) per source. Metric names must match those reported by the flow. Declare up to 32 sources per panel.
- The default mode, `latest`, reads the most recent saved run and the metrics reported with `record_metrics`. In `live` mode, `status`, `started_at`, and metrics reported with `publish_metrics` are available while the run is active. Use `latest` for the finished time and recorded duration.
- Call `workspaceDashboard.subscribe(({ sources }) => { /* render */ })` in `dashboard.js`. Each source has `id`, `sheet`, `state`, and `value`. Phyzflow adds `sheet_name` when it resolves the sheet, and `run_id` and `timestamp` when a run is available. The `state` is `available`, `no_data`, `sheet_unavailable`, or `source_unavailable`. Treat a `null` value as missing.

See the [multi-sheet dashboard example](../workspace-test/sheets-demo/dashboards/workspace/) for a responsive duration comparison and live metric cards. Add `workspace-test/sheets-demo` as a Workspace in the installed app, start **Run All**, and open **Dashboard**. The Live Counter publishes continuously. Telemetry Control Room reports its duration after it finishes.

### Add a control

Declare a `NodeSetting` in Python, include the same object in the node's `Step.settings` mapping, and call its `get()` method in the node. The default determines the supported value type. Numeric settings can declare a minimum and maximum. Use settings for operational values visible in the dashboard.

On **Save**, call `workspaceDashboard.setSetting(nodeId, name, value, "next")` and await the result. A successful response has status `saved`. Workspace locally persists the value and snapshots saved settings when the next UI or CLI run starts. Invalid requests return `rejected` with a message.

For a setting declared with `live=True`, the dashboard can send mode `"live"` when viewing an active node. The response includes an ID and status `queued`, or a terminal result if the node already responded. Call `apply_node_settings()` in the node at a safe point before reading the setting for subsequent work. Match the response ID to `settingChanges` to display `applied` or `rejected`. An applied change includes the run ID and timestamp and lasts for that run.

## Inspect a project through a local agent

Use the authenticated local development agent to inspect configured projects without a browser. See [Workspace development agent](../docs/workspace-agent.md) for startup, client usage, limits, and the request contract.

## Report release and module versions

Run `workspace version --json` in the environment where the release wheel is installed. The manifest identifies the installed release and Git commit and reports each module's `fingerprint` and `last_changed_version`. Run `workspace version` for a text summary.

Build a release wheel from the committed SDK source with Python 3.12 or newer and uv:

```sh
python workspace/release/build_wheel.py --out-dir workspace/dist
```

Use this command without `--previous-manifest` to establish the first baseline. For subsequent builds, pass `--previous-manifest` with the JSON reported by the preceding published wheel. The builder carries forward the last-changed release for unchanged modules. Direct source and editable installs do not include a release manifest.

Fingerprints hash the packaged file paths and contents by module. The release builder maps the CLI, Runner, Web UI, and MCP files explicitly and assigns the other SDK files to the Application. When adding a file outside the Application, update its module assignment in the builder. Wheel metadata and the manifest itself are excluded from the module fingerprints.

## Verify a release candidate

With Python 3.12, uv, and Google Chrome installed, check a candidate from the repository that contains its Git commit.

```sh
wheel=$(python workspace/release/build_wheel.py --out-dir /tmp/phyzflow-candidate)
python workspace/release/check_compatibility.py "$wheel" --report /tmp/phyzflow-candidate/compatibility.json
```

The check installs the wheel in a temporary environment and runs the compatibility tests from the candidate's commit. The JSON report identifies the release, commit, and wheel SHA-256, and records the Workspace format, Sheet Worker protocol, and Dashboard Bridge combinations with their test results. A failed test or an advertised version without a test blocks the release. Update the case list and its tests whenever an interface adds an advertised version.

The macOS release includes `Workspace.app/Contents/Resources/phyzflow-compatibility.json` in the signed bundle. After publishing the ZIP in the output directory, the build writes a neighboring `*.zip.compatibility.json` with the ZIP's SHA-256 checksum and its UTC publication time.
