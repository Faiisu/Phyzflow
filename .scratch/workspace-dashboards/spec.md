# Workflow and node dashboards

Workspace is a local place to observe and control an inference workflow. Developers and AI agents write ordinary HTML, CSS, and JavaScript in the workflow project. Users open these dashboards in the Flow Editor, inspect saved and live runs, and change settings explicitly exposed by Python.

## User experience

- Open Visualize from the node inspector or workflow toolbar. Each scope loads its own project files and can refresh them after an edit.
- Pick a saved run or follow the current run in the same dashboard. Node views receive only the selected node's execution data. The workflow overview receives the graph and execution data for its nodes.
- Apply a validated setting to the next run. A live-capable node can also accept a change for the current run at a checkpoint in its Python code.
- Use Telemetry Control Room to demonstrate timing, queue measurements, and a setting that changes subsequent work.

## Constraints

- Extend the local Flow Editor and Run History path in development milestone 4. Reuse the existing Runner, graph and run stream.
- Run dashboard code in an isolated browser frame. Workspace mediates data and setting changes. Dashboard assets are local to the selected scope, and the asset loader rejects paths outside that scope.
- Dashboard data defaults to operational metadata. Preview data is explicit and ephemeral. Requests, samples, and traceback text stay out of the dashboard data channel.
- Saved settings are snapshotted when a run starts. A live change is scoped to one run and requires acknowledgement from the node.

## Verification

The installed SDK and sample consumer passed all 34 local tests through Python, the CLI, and Chrome. The dashboard tests exercised both single-flow and sheet-based Flow Editors. The [SDK guide](../../workspace/README.md#build-a-dashboard-for-a-workflow-or-node) documents the file and JavaScript interface. See [Telemetry Control Room](../../workspace-test/sheets-demo/flows/Telemetry%20Control%20Room/README.md) for the dashboard and setting example.
