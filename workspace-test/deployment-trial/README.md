# Support ticket deployment

The target is a Linux ARM64 CPU container on CPython 3.12. The image trains a scikit-learn multinomial Naive Bayes classifier on synthetic labeled tickets during the build. It installs the Workspace SDK and pinned native NumPy, SciPy, and scikit-learn dependencies. The first prediction loads the artifact locally and later requests reuse it.

## Deploy and verify

Use a Docker engine that can run Linux ARM64 images. From the repository root, reuse the `.venv` from the [local demo](../README.md#local-demo) or install the SDK in a new environment:

```sh
uv venv --python 3.12
uv pip install ./workspace
```

From the repository root:

```sh
cd workspace-test/deployment-trial
../../.venv/bin/workspace deploy --sdk ../../workspace
```

Set deployment defaults in the project's [setting.json](setting.json). The top-level `port` setting continues to control the Flow Editor.

| Key under `deploy` | Default | Purpose |
| --- | --- | --- |
| `ipv4` | `127.0.0.1` | IPv4 address on the Docker host. Use `0.0.0.0` to publish on all IPv4 interfaces. |
| `port` | `8080` | Published host port. Set it to `0` to let Docker choose a free port. |
| `container_port` | `8000` | Port the HTTP service listens on inside the container. Pre-publication smoke tests use this port too. |
| `context` | `null` | Docker context name. Set it to `colima` to select that engine, or leave it null to use the current context. |

Override those values for a single deployment with `--ipv4`, `--port`, `--container-port`, or `--context`. For example:

```sh
../../.venv/bin/workspace deploy --sdk ../../workspace --context colima --port 9090
```

The command builds the Dockerfile and runs `workspace check` on the exact image. If the project includes a `smoke_test.py`, the image must include it in the working directory. The smoke test calls the same prediction handler used in service, from a candidate container with no published ports or external network. Once the checks pass, the command publishes the image at the configured address and returns `build_id`, `container_id`, `url`, `ipv4`, `port`, and `container_port` as JSON. When binding to `0.0.0.0`, the reported URL uses loopback for requests from the same machine. Clients on the network use the Docker host's reachable IP address and the reported host port. Build and check output goes to standard error.

At the default URL:

```sh
curl --fail-with-body http://127.0.0.1:8080/predict \
  -H 'Content-Type: application/json' \
  --data '{"text":"Urgent: duplicate invoice charge"}'
python3 smoke_test.py http://127.0.0.1:8080
```

`POST /predict` accepts a JSON object with non-empty `text`. The example returns HTTP 200 with `{"category":"billing","priority":"urgent"}`. The smoke test checks three held-out tickets and invalid input.

## Trace a failed run

Send the trial's failure input with the same request format:

```sh
curl -i http://127.0.0.1:8080/predict \
  -H 'Content-Type: application/json' \
  --data '{"text":"Debug failure: example details"}'
```

The HTTP 500 response includes `run_id`. In the container logs, find the JSON record with that ID to see the build ID, node, source file, line, and exception type. The service records these fields and HTTP status codes. Copy the container ID from the deploy result:

```sh
container_id=REPLACE_WITH_CONTAINER_ID
docker logs "$container_id"
docker stop "$container_id"
```

Use the same Docker context for these commands. If you passed `--context colima` during deployment, add it after `docker` in both commands.

The Docker integration test builds the serving image and exercises the CLI and HTTP endpoint. Run it from this directory with the same local environment and Docker context:

```sh
WORKSPACE_DOCKER_CONTEXT=colima ../../.venv/bin/python test_deploy.py -v
```

## Benchmark the trained workload

Install Google Chrome. From the repository root, use the pinned model dependencies and train the local artifact with the same script and training data as the deployment image:

```sh
uv venv --python 3.12.11
uv pip install ./workspace './workspace-test[verification]' -r workspace-test/deployment-trial/requirements.txt
cd workspace-test
../.venv/bin/python deployment-trial/train_model.py
for repeat in 1 2 3; do
  for mode in direct runner ui; do
    ../.venv/bin/python benchmark.py "$mode" --workload trained --warmup 20 --runs 100 || exit 1
  done
done
```

Run the modes sequentially on the same machine and interpreter. All three run the same held-out request from [sample.json](sample.json) and validate the expected prediction on every run. Direct mode calls the node functions in order and Runner mode has no event observer. Live UI latency spans the Run action in Chrome until the stream has finished and the output is available in the page. Each mode warms the model before measuring latency. p50 is the median and p95 uses nearest rank. Peak RSS is the high-water mark in MiB of the Python process running the model, including startup and warm-up. For live UI this is the CLI server process. Chrome and the Playwright driver are outside that memory scope.

### Reference measurements

Three rounds on an Apple M2 CPU with macOS 27.0.1 (arm64), CPython 3.12.11, Chrome 157.0.8103.51, Playwright 1.65.2, and the [pinned model dependencies](requirements.txt) produced the following results. This reference environment measures local Workspace use with the model deployed to Linux ARM64.

| Round | Mode | p50 (ms) | p95 (ms) | Peak RSS (MiB) |
| --- | --- | ---: | ---: | ---: |
| 1 | Direct | 0.048583 | 0.068917 | 98.625 |
| 1 | Runner | 0.067562 | 0.072417 | 98.641 |
| 1 | Runner with live UI | 1.2 | 1.4 | 99.219 |
| 2 | Direct | 0.047855 | 0.053334 | 98.656 |
| 2 | Runner | 0.068292 | 0.092334 | 98.734 |
| 2 | Runner with live UI | 1.2 | 1.6 | 99.328 |
| 3 | Direct | 0.050021 | 0.067542 | 98.672 |
| 3 | Runner | 0.068125 | 0.072916 | 98.672 |
| 3 | Runner with live UI | 1.2 | 1.4 | 99.234 |

### Release limits

Repeat the three rounds in the reference environment for the release check. In each round, subtract that round's direct p95 and peak RSS from the corresponding values for each Runner mode. Each difference must be at or below the matching limit in every round. The limits leave headroom for measurement variation and the local run history planned for the release.

| Mode | Largest measured p95 increase (ms) | Added p95 limit (ms) | Largest measured peak RSS increase (MiB) | Added peak RSS limit (MiB) |
| --- | ---: | ---: | ---: | ---: |
| Runner | 0.039 | 0.25 | 0.078 | 8 |
| Runner with live UI | 1.547 | 5.0 | 0.672 | 16 |
