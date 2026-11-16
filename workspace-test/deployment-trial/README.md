# Support ticket deployment trial

The first target is a Linux ARM64 CPU container on CPython 3.12. This trial trains a scikit-learn multinomial Naive Bayes text classifier on synthetic labeled support tickets during the image build. It exercises the installed SDK and native NumPy, SciPy, and scikit-learn wheels. It is a serving trial for the three sample categories: access, billing, and technical.

The image trains its own trusted model artifact. `workspace check` runs before training during the build and again at container startup. The first prediction loads the artifact locally and later requests reuse it. Training and prediction require no GPU, external model service, or runtime model download.

Run from the repository root, using a Docker context connected to a Linux engine. For the verified Colima setup, replace each `docker` with `docker --context colima`.

```sh
docker build --platform linux/arm64 --no-cache --build-context sdk=./workspace \
  -t workspace-ticket-trial workspace-test/deployment-trial
docker run --rm --platform linux/arm64 workspace-ticket-trial workspace check
docker run --rm --platform linux/arm64 -p 127.0.0.1:8080:8000 workspace-ticket-trial
```

When the server prints `Serving predictions on port 8000.`, send a JSON request from another terminal:

```sh
curl --fail-with-body http://127.0.0.1:8080/predict \
  -H 'Content-Type: application/json' \
  --data '{"text":"Urgent: duplicate invoice charge"}'
python3 workspace-test/deployment-trial/smoke_test.py http://127.0.0.1:8080
```

`POST /predict` accepts a non-empty `text` string in a JSON object. The request above returns HTTP 200 with `{"category":"billing","priority":"urgent"}`. The smoke test checks three held-out tickets and a request with the wrong input type. Stop the server with Ctrl-C.
