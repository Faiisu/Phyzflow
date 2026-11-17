# Python Flow Workspace

A local workspace for humans and AI agents to build, run, and inspect Python flows. Each sheet opens a Python flow as an execution graph where you can run the flow, inspect individual nodes, and trace results back to source code.

## Quick start

### Launcher scripts

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and make it available on your PATH. From the repository root, run the launcher for your platform.

**Linux**

```sh
./run-workspace.sh
```

**Windows PowerShell**

```powershell
.\run-workspace.bat
```

The launchers create a local `default-workspace/` with a runnable **Main** sheet on first use. Work on your flows in this directory. Git ignores it, and later launches reuse its files. The launcher creates a Python 3.12 virtual environment if the project lacks one and installs the local SDK into its `.venv`. Install any flow-specific dependencies in the same environment. Open the URL printed in the terminal and choose **Run flow**.

Pass a project directory to open another workspace. Open the development sheets explicitly with `./run-workspace.sh workspace-test/sheets-demo` on Linux or `.\run-workspace.bat workspace-test/sheets-demo` in Windows PowerShell.

Both scripts ask for the bind IPv4 address and port on startup. Press Enter at either prompt to use the value from `setting.json` or the built-in default of `127.0.0.1` and port `18080`. Entries apply to the current launch. Use port `0` to pick an available port. Use a non-loopback address or `0.0.0.0` on a trusted network where others may edit the project and run flows.

### Run the ticket example from the CLI

From the repository root, use [uv](https://docs.astral.sh/uv/getting-started/installation/) to install the Workspace SDK into a Python 3.12 environment.

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python ./workspace
.venv/bin/workspace --project workspace-test ui
```

Open the URL printed in the terminal (usually [127.0.0.1:18080](http://127.0.0.1:18080)). Select the **Main** sheet and click **Run flow** to try the sample. Click **+** to create a sheet with its own `flow.py`. After editing Python, use **Reload flow** to refresh that sheet.

To try the dashboard examples, run `.venv/bin/workspace --project workspace-test/sheets-demo ui` and open [Live Counter](workspace-test/sheets-demo/flows/Live%20Counter/README.md) or [Telemetry Control Room](workspace-test/sheets-demo/flows/Telemetry%20Control%20Room/README.md). Select **Visualize** in the toolbar to open the workflow dashboard.

## Documentation

- [macOS launcher and CLI commands for AI agents](workspace/README.md)
- [Sample flows, configuration, and tests](workspace-test/README.md)
- [Deploy the sample flow to Docker on Linux ARM64](workspace-test/deployment-trial/README.md#deploy-and-verify)
- [Development plan](docs/development-plan.md)
