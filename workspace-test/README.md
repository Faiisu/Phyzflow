# Workspace testing

This directory contains the sample consumer project, tests, and fixtures for [Workspace](../workspace/). Give the sample project its own `pyproject.toml` and install the Workspace SDK as a dependency in a clean Python environment.

Use the sample project to verify installation, flow execution, debugging, and deployment through the public Python API, CLI, and Web UI as each becomes available.

## Local demo

The first workload is support-ticket classification. The development environment is Python 3.12. The [deployment workload](deployment-trial/README.md) runs a trained model in a Linux ARM64 CPU container with Python 3.12.

With `uv` available on your PATH, install the SDK and its sample consumer into a new environment from the repository root:

```sh
uv venv --python 3.12
uv pip install ./workspace ./workspace-test
cd workspace-test
../.venv/bin/workspace check
../.venv/bin/workspace run '{"text":"Urgent: duplicate invoice"}'
../.venv/bin/python -c 'from flow import flow; from workspace_sdk import run; print(run(flow, {"text": "Urgent: duplicate invoice"}).output)'
../.venv/bin/workspace run '{"text":"Debug failure"}'
```

The successful response is `{"category":"billing","priority":"urgent"}`. The debug input fails in the predictor's helper and skips `Decide`.

From the same directory, start the Web UI and open the printed loopback URL:

```sh
../.venv/bin/workspace ui
```

The UI listens at `http://127.0.0.1:18080` by default. To change the port, place `setting.json` in the directory where you start Workspace:

```json
{
  "port": 18080,
  "zoom": {
    "speed": 1.0,
    "smooth_ms": 160
  }
}
```

Restart the UI after editing the file. Use `workspace ui --port <number>` to override the port for one launch, or `workspace ui --port 0` to let the OS pick an available port.

Scroll up or down over the canvas to zoom around the pointer. Each wheel event changes the zoom immediately, including when you reverse direction. Set `zoom.speed` to a multiplier greater than 0 and at most 10 (default 1.0). Set `zoom.smooth_ms` to the duration of the remaining animation in milliseconds (0–1000, default 160). Set it to 0 to apply the entire change immediately. All sheets use the workspace root settings.

Drag empty canvas space to move the flow. Use **Panels** to show or hide each overlay independently, and **Center** in the zoom controls to bring the graph back to the middle. Select a node on the canvas or in Flow outline to see its inputs, downstream nodes, and source location in Inspector.

Use Run flow in the toolbar to run the prefilled sample. In the Run panel, replace the input with `{"text":"Debug failure"}` and run again. Open the traceback in Run details to find the failing predictor helper. After editing project source, stop and restart the UI to load the changes.

### Add and debug a node

In `workspace-test/`, create the `ticket_demo/routing/` directory with an empty `__init__.py`. Add `ticket_demo/routing/queues.py`:

```python
def route(prepared: str, decision: dict[str, str]) -> dict[str, str]:
    if prepared == "debug routing":
        raise ValueError("No support queue is configured for this ticket.")
    queue = f"{decision['category']}-{decision['priority']}"
    return {**decision, "queue": queue}
```

In `flow.py`, import the new function with `from ticket_demo.routing.queues import route`. Append this step to the `steps` tuple after `Decide` and change the flow output to `Output("Route")`:

```python
Step("Route", route, {"prepared": Output("Prepare"), "decision": Output("Decide")}),
```

Stop the previous UI with Ctrl+C. Check and run the edited flow, then start the UI again:

```sh
../.venv/bin/workspace check
../.venv/bin/workspace run '{"text":"Urgent: duplicate invoice"}'
../.venv/bin/workspace ui
```

The output now includes `"queue":"billing-urgent"`. In the UI, run the prefilled ticket and check that output. Then run `{"text":"Debug routing"}` and use Run details to identify the failed node, the exception, and the file and line where it originated.

### Recover a completed run

You can close the browser tab while a run is in progress. Leave the `workspace ui` process running, then reopen its URL. Open **History** from **Panels** and select a saved run to see its status, duration, source version, and node summaries. Use **Refresh history** if the run finishes after you reopen the page.

Workspace saves completed runs in `.workspace/runs/` inside the project, so the same history is available after restarting the UI. Each source version identifies the project Python code loaded by Workspace, including uncommitted edits. History stores input and output metadata such as value type and tensor shape, dtype, and device. Saved failures show the exception type and project stack locations. Request values and exception messages are absent from history by default.

### Diagnose an incorrect result

Stop the previous UI with Ctrl+C. From `workspace-test/`, start the evidence demo in its own project directory:

```sh
cd evidence-demo
../../.venv/bin/workspace ui
```

Open the printed URL and run the prefilled ticket. Its category should be `technical`. The flow reports **Succeeded**, but the output says `billing`. Open **History** from **Panels**, select **Refresh history**, and open the successful run to find the first node that returned the wrong value.

<details>
<summary>Check the diagnosis</summary>

**Predict** received the normalized ticket about a blank screen and returned `billing`. Its decision reason says the demo predictor assigns every ticket to billing. **Decide** received that prediction and applied the priority rule.

</details>

To store a reason in your own node, call `record_reason("…")` from `workspace_sdk` with an explanation safe to save. To also store examples, set `capture_samples=True` on that node's `Step`. Only enable capture where the values are safe to retain. Each JSON example is limited to 4 KiB. Oversized or unsupported values retain their metadata and show that a sample is unavailable. History keeps up to ten reasons per node, each up to 512 characters.

From the repository root, follow [Deploy and verify](deployment-trial/README.md#deploy-and-verify) to try the trained model on Docker.

## Multiple flows in one workspace

Open `workspace ui` from your workspace directory and select **+** in the bottom bar. Enter a sheet name and select **Create sheet**. Workspace opens the new sheet's runnable starter. Edit the graph in `flow.py` and the Start node in `nodes/start/entrypoint.py`, then select **Reload flow**. If that sheet is running, wait for it to finish or stop it before reloading. See [one folder per node](../workspace/README.md#one-folder-per-node) for the standard layout.

You can also add flow directories directly. Put each entry point under `flows/<sheet-name>/flow.py`, with an optional `sample.json` and `pyproject.toml` beside it. The directory name becomes the sheet label. A root `flow.py` appears as the **Main** sheet. To open just the flow in the current directory, use `workspace ui --single-flow`.

Try two independent sheets from the repository root:

```sh
cd workspace-test/sheets-demo
../../.venv/bin/workspace ui
```

Use the bottom tabs to switch sheets. The Workspace and all sheet views use the same browser address and port. Run either flow from its toolbar and watch its tab status while viewing another sheet. Select **Stop** on a running sheet to terminate that sheet's Python process. Select the stopped sheet and **Open flow** to start it again. A terminated run does not produce a completed history record. Each sheet keeps its completed runs in its own `.workspace/runs/` directory.

Select **×** next to a sheet's **Stop** button and confirm **Delete sheet**. Workspace stops that sheet's process and moves its entire folder to `.workspace/deleted-flows/<archive-id>/<sheet-name>/`. To restore it, move the sheet folder back under `flows/` and restart the Workspace UI. The root **Main** sheet has no delete button.

Git tracks [Live Counter](sheets-demo/flows/Live%20Counter/README.md) and [Telemetry Control Room](sheets-demo/flows/Telemetry%20Control%20Room/README.md) as shared examples. Additional sheets created under `sheets-demo/flows/` stay local to your checkout.

To check whether Workspace found a custom sheet such as **Whisper Medium ASR**, run `.venv/bin/workspace --project workspace-test/sheets-demo sheets` from the repository root. The output lists each discovered sheet and its `flow.py` path.

## Acceptance tests and overhead baseline

Run the verification suite and demo benchmark in a checkout with the original sample flow, before the node exercise or in a separate checkout. Install Google Chrome and the optional test dependencies. From the repository root:

```sh
uv pip install './workspace[mcp]' './workspace-test[verification]'
cd workspace-test
../.venv/bin/python -m unittest discover -s tests -v
../.venv/bin/python benchmark.py direct
../.venv/bin/python benchmark.py runner
../.venv/bin/python benchmark.py ui
```

Run the three modes sequentially on the same machine and Python environment. Each mode checks the expected output for the same sample input. The defaults are 20 warm-up runs and 100 measured runs. Direct mode calls the same node functions. Runner mode has no event observer. UI mode measures from the browser's Run action until the stream finishes and the output is available in the UI. Peak RSS is the high-water mark of the Python execution process, including startup. It excludes Chrome and the Playwright driver.

Two consecutive runs on Apple Silicon, macOS 27.0.1, CPython 3.12.11, and Chrome 157.0.8103.51 produced these ranges for the demo:

| Mode | p50 (ms) | p95 (ms) | Peak RSS (MiB) |
| --- | ---: | ---: | ---: |
| Direct | 0.000250 | 0.000292 | 23.109–23.156 |
| Runner | 0.010167–0.010375 | 0.011250–0.013333 | 23.156–23.172 |
| Runner with live UI | 1.0 | 1.3–1.4 | 26.953–27.000 |

For the real model and release overhead limits, see [Benchmark the trained workload](deployment-trial/README.md#benchmark-the-trained-workload).
