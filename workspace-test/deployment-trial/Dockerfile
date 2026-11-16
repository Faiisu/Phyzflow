FROM python:3.12-slim-bookworm

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=sdk pyproject.toml /sdk/pyproject.toml
COPY --from=sdk src /sdk/src
RUN pip install --no-cache-dir /sdk

COPY flow.py serve.py train_model.py training.json smoke_test.py ./
RUN workspace check && python train_model.py

USER 65534:65534
EXPOSE 8000
STOPSIGNAL SIGINT
CMD ["sh", "-c", "workspace check && exec python serve.py"]
