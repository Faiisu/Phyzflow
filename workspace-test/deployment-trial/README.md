# Support ticket deployment

The target is a Linux ARM64 CPU container on CPython 3.12. The image trains a scikit-learn multinomial Naive Bayes classifier on synthetic labeled tickets during the build. It installs the Workspace SDK and pinned native NumPy, SciPy, and scikit-learn dependencies. The first prediction loads the artifact locally and later requests reuse it.

## Deploy and verify

Install the Workspace SDK in a local environment with access to a Docker engine that can run Linux ARM64 images. From the repository root:

```sh
uv venv --python 3.12
uv pip install ./workspace
cd workspace-test/deployment-trial
../../.venv/bin/workspace deploy --sdk ../../workspace --context colima
```

Omit `--context colima` when the selected Docker context already points to the target engine. The default host port is 8080. Use `--port 0` to let Docker choose a free port, or `--port` with an explicit port.

The command builds the Dockerfile and runs `workspace check` on the exact image. If the project includes a `smoke_test.py`, the image must include it in the working directory. The smoke test calls the same prediction handler used in service, from a candidate container with no published ports or external network. Once the checks pass, the command serves that image on the host's IPv4 loopback address. The final JSON reports `build_id`, `container_id`, and `url`. Build and check output goes to standard error.

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
docker --context colima logs "$container_id"
docker --context colima stop "$container_id"
```

The Docker integration test builds the serving image and exercises the CLI and HTTP endpoint. Run it from this directory with the same local environment and Docker context:

```sh
WORKSPACE_DOCKER_CONTEXT=colima ../../.venv/bin/python test_deploy.py -v
```
