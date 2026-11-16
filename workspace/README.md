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
