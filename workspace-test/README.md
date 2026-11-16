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

Select a node on the canvas or in Flow outline to see its inputs, downstream nodes, and source location in Inspector. Use Run flow in the toolbar to run the prefilled sample. In the Run tab, replace the input with `{"text":"Debug failure"}` and run again. Open the traceback in Run details to find the failing predictor helper. After editing project source, stop and restart the UI to load the changes.

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

You can close the browser tab while a run is in progress. Leave the `workspace ui` process running, then reopen its URL. Open the **History** tab and select a saved run to see its status, duration, source version, and node summaries. Use **Refresh history** if the run finishes after you reopen the page.

Workspace saves completed runs in `.workspace/runs/` inside the project, so the same history is available after restarting the UI. Each source version identifies the project Python code loaded by Workspace, including uncommitted edits. History stores input and output metadata such as value type and tensor shape, dtype, and device. Saved failures show the exception type and project stack locations. Request values and exception messages are absent from history by default.

### Diagnose an incorrect result

Stop the previous UI with Ctrl+C. From `workspace-test/`, start the evidence demo in its own project directory:

```sh
cd evidence-demo
../../.venv/bin/workspace ui
```

Open the printed URL and run the prefilled ticket. Its category should be `technical`. The flow reports **Succeeded**, but the output says `billing`. Open the **History** tab, select **Refresh history**, and open the successful run to find the first node that returned the wrong value.

<details>
<summary>Check the diagnosis</summary>

**Predict** received the normalized ticket about a blank screen and returned `billing`. Its decision reason says the demo predictor assigns every ticket to billing. **Decide** received that prediction and applied the priority rule.

</details>

To store a reason in your own node, call `record_reason("…")` from `workspace_sdk` with an explanation safe to save. To also store examples, set `capture_samples=True` on that node's `Step`. Only enable capture where the values are safe to retain. Each JSON example is limited to 4 KiB. Oversized or unsupported values retain their metadata and show that a sample is unavailable. History keeps up to ten reasons per node, each up to 512 characters.

From the repository root, follow [Deploy and verify](deployment-trial/README.md#deploy-and-verify) to try the trained model on Docker.

## Acceptance tests and overhead baseline

Run the verification suite and demo benchmark in a checkout with the original sample flow, before the node exercise or in a separate checkout. Install Google Chrome and the optional test dependencies. From the repository root:

```sh
uv pip install ./workspace './workspace-test[verification]'
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
