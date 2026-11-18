# Workspace development

This directory contains the Workspace SDK, Runner, CLI, Web UI, and deployment tooling. The SDK is installed as a dependency by the sample project in [`workspace-test/`](../workspace-test/).

See the [development plan](../docs/development-plan.md) for the implementation order and acceptance criteria.

## Open a local Workspace on macOS

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/). On first use, uv can download Python 3.12 if it is unavailable.
2. Unzip the Workspace release for your Mac's architecture and open `Workspace.app`.
3. Choose an existing Workspace or an empty folder. The launcher uses the project's `.venv`, installs the SDK from the release, and opens the configured local URL in your browser. Select **+** to create a sheet, then **Run flow**.
4. Share the project folder path with an AI agent or open it in your IDE. If startup fails, the launcher shows the error. UI logs are in the project's `.workspace/launcher-*.log`. Edit the project's `setting.json` to resolve a port conflict.

Build a release on macOS from the repository root with `sh workspace/release/macos/build.sh`. The ZIP is written to `workspace/dist/` by default. The build uses the local Mac architecture and ad-hoc signing for local use.

## Work on a selected project from the CLI

Use the `workspace` executable installed in the project's Python environment. Run these commands from any directory:

```sh
workspace --project "$project_path" sheets
workspace --project "$project_path" check --sheet "First Flow"
workspace --project "$project_path" run --sheet "First Flow" '{}'
workspace --project "$project_path" ui
```

Use `workspace init "$project_path"` to initialize a new project and `workspace --project "$project_path" new-sheet "First Flow"` to create a runnable Python sheet. The CLI prints the path to its Python entry point. A browser already open on the project discovers the new sheet without a restart. After editing Python, select the sheet and click **Reload flow** to refresh the graph and use the edited code on the next run.

In the Workspace, select a sheet and choose **Edit flow.py** to open its flow source. **Save** writes to the project file. If another editor changed the file, copy any changes you want to keep and reopen the editor to see the latest version. If **Reload flow** reports a validation error, use the diagnostic to locate it. You can reopen **Edit flow.py** to fix the flow source, save it, and retry **Reload flow**.

Open sheets reload automatically after Python files in their project folders change and the edits settle. The next run uses the reloaded code. If a run is active or the source editor has unsaved text, the sheet shows **Reload pending** and waits. If validation fails, the sheet shows the diagnostic and retries after another source change. To resume an explicitly stopped sheet, select **Open flow**.

Use `workspace init --with-main "$project_path"` to start with a runnable **Main** sheet. The Linux and Windows launchers use this command to initialize the local `default-workspace/`.

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

## Build a dashboard for a workflow or node

Write or have an AI agent generate `index.html`, optionally with `style.css`, `setting.js`, and `dashboard.js`, in the workflow's `dashboards/workflow/` or `dashboards/nodes/<step-id>/` folder. Use the exact step ID for ordinary folder names, including spaces. Percent-encode reserved path characters and non-ASCII characters using UTF-8, and encode the entire name for `.` and `..` as `%2E` and `%2E%2E`. Workspace loads the companion files automatically and executes `setting.js` before `dashboard.js`. Use inline assets, data images, or browser APIs such as canvas. The browser policy restricts resource loads and navigation and isolates the frame from Workspace's page.

Open **Visualize** in the workflow toolbar or the node inspector. Choose a saved run or **Live run**. Click **Reload dashboard** to load file changes without restarting the flow. The live view reconnects to an active run after a browser refresh, restoring node status and the latest previews, metrics, and frames. Saved runs remain available after reopening Workspace. See [Telemetry Control Room](../workspace-test/sheets-demo/flows/Telemetry%20Control%20Room/README.md) for a working example.

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

### Add a control

Declare a `NodeSetting` in Python, include the same object in the node's `Step.settings` mapping, and call its `get()` method in the node. The default determines the supported value type. Numeric settings can declare a minimum and maximum. Use settings for operational values visible in the dashboard.

On **Save**, call `workspaceDashboard.setSetting(nodeId, name, value, "next")` and await the result. A successful response has status `saved`. Workspace locally persists the value and snapshots saved settings when the next UI or CLI run starts. Invalid requests return `rejected` with a message.

For a setting declared with `live=True`, the dashboard can send mode `"live"` when viewing an active node. The response includes an ID and status `queued`, or a terminal result if the node already responded. Call `apply_node_settings()` in the node at a safe point before reading the setting for subsequent work. Match the response ID to `settingChanges` to display `applied` or `rejected`. An applied change includes the run ID and timestamp and lasts for that run.

## Inspect a project through a local agent

Use the authenticated local development agent to inspect configured projects without a browser. See [Workspace development agent](../docs/workspace-agent.md) for startup, client usage, limits, and the request contract.
