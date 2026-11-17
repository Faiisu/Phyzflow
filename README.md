# Python Flow Workspace

A local workspace for humans and AI agents to build, run, and inspect Python flows. Each sheet opens a Python flow as an execution graph where you can run the flow, inspect individual nodes, and trace results back to source code.

## Quick start

From the repository root, use [uv](https://docs.astral.sh/uv/getting-started/installation/) to install the Workspace SDK into a Python 3.12 environment.

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python ./workspace
.venv/bin/workspace --project workspace-test ui
```

Open the URL printed in the terminal (usually [127.0.0.1:18080](http://127.0.0.1:18080)). Select the **Main** sheet and click **Run flow** to try the sample. Click **+** to create a sheet with its own `flow.py`. After editing Python, use **Reload flow** to refresh that sheet.

To try the dashboards, run `.venv/bin/workspace --project workspace-test/sheets-demo ui` and open [Dashboard Demo](workspace-test/sheets-demo/flows/Dashboard%20Demo/README.md). Click **Visualize** in the toolbar for the overview or in the **Collect readings** node inspector for live progress.

## Documentation

- [macOS launcher and CLI commands for AI agents](workspace/README.md)
- [Sample flows, configuration, and tests](workspace-test/README.md)
- [Deploy the sample flow to Docker on Linux ARM64](workspace-test/deployment-trial/README.md#deploy-and-verify)
- [Development plan](docs/development-plan.md)
