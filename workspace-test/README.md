# Workspace testing

This directory contains the sample consumer project, tests, and fixtures for [Workspace](../workspace/). Give the sample project its own `pyproject.toml` and install the Workspace SDK as a dependency in a clean Python environment.

Use the sample project to verify installation, flow execution, debugging, and deployment through the public Python API, CLI, and Web UI as each becomes available.

## Local demo

The first workload is support-ticket classification. The development environment is Python 3.12. The candidate deployment target is a single Linux CPU container with Python 3.12. That target will be verified separately with the real-model workload.

From the repository root, install the SDK and its sample consumer into a new environment:

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

Run the prefilled sample, then replace the input with `{"text":"Debug failure"}` and run again. Open the traceback in Run details to find the failing predictor helper. After editing project source, stop and restart the UI to load the changes.

## Acceptance tests and overhead baseline

To run the verification suite and benchmark, install Google Chrome and the optional test dependencies. From the repository root:

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
