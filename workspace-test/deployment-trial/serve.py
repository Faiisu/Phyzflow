"""Serve the trial flow in its Linux container."""

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sys

from workspace_sdk import run

from flow import flow


BUILD_ID = os.environ.get("WORKSPACE_BUILD_ID", "local-trial")


def report_failure(event: dict) -> None:
    if event["status"] != "failed" or "step_id" not in event:
        return
    file = Path(event["file"])
    print(json.dumps({
        "build_id": BUILD_ID,
        "run_id": event["run_id"],
        "step_id": event["step_id"],
        "file": str(file.relative_to(Path.cwd(), walk_up=True)),
        "line": event["line"],
        "error_type": event["error_type"],
    }), file=sys.stderr, flush=True)


class Handler(BaseHTTPRequestHandler):
    timeout = 5

    def send_json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(HTTPStatus.OK, {"status": "ready"})
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Use POST /predict."})

    def do_POST(self) -> None:
        if self.path != "/predict":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Use POST /predict."})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Send application/json."})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
        except (ValueError, TimeoutError):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Send valid JSON up to 64 KiB."})
            return

        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip():
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Provide a non-empty text string."})
            return

        result = run(flow, {"text": text}, report_failure)
        if result.status == "failed":
            self.send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "Prediction failed.", "run_id": result.id},
            )
            return
        self.send_json(HTTPStatus.OK, result.output)

    def log_error(self, format: str, *args: object) -> None:
        self.log_message("HTTP protocol error.")

    def log_request(self, code="-", size="-") -> None:
        self.log_message("HTTP %s", code)


if __name__ == "__main__":
    with HTTPServer(("0.0.0.0", 8000), Handler) as server:
        print("Serving predictions on port 8000.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
