from collections import deque
from http.client import HTTPConnection, HTTPException
import json
from threading import Condition
from uuid import uuid4


class SheetRun:
    def __init__(self, inputs: dict | None, *, retry_count: int = 0) -> None:
        self.run_id = uuid4().hex
        self.inputs = inputs
        self.retry_count = retry_count
        self.crash_exhausted = False
        self.status = "running"
        self.start_error: str | None = None
        self._condition = Condition()
        self._lines: deque[bytes] = deque(maxlen=256)
        self._count = 0

    def publish(self, line: bytes) -> None:
        with self._condition:
            self._lines.append(line)
            self._count += 1
            self._condition.notify_all()

    def finish(self, status: str, error: str | None = None) -> None:
        with self._condition:
            if self.status != "running":
                return
            self.status = status
            self.start_error = error
            if error:
                self.publish(json.dumps({"run_id": self.run_id, "error": error}).encode() + b"\n")
            self._condition.notify_all()

    def messages(self):
        cursor = 0
        while True:
            with self._condition:
                self._condition.wait_for(lambda: cursor < self._count or self.status != "running")
                cursor = max(cursor, self._count - len(self._lines))
                if cursor == self._count:
                    return
                line = self._lines[cursor - self._count + len(self._lines)]
                cursor += 1
            yield line

    def collect(self, url: str, token: str) -> tuple[str | None, str | None]:
        connection = HTTPConnection(url.removeprefix("http://"), timeout=2)
        headers = {
            "Content-Type": "application/json",
            "X-Phyzflow-Worker-Token": token,
            "X-Phyzflow-Run-Id": self.run_id,
        }
        terminal = None
        reported = None
        try:
            connection.connect()
            socket = connection.sock
            connection.request("POST", "/run", body=json.dumps(self.inputs or {}).encode(), headers=headers)
            with connection.getresponse() as response:
                if response.status != 200:
                    return "failed", f"The flow could not start ({response.status})."
                socket.settimeout(None)
                for line in response:
                    message = json.loads(line)
                    self.publish(line)
                    if "result" in message:
                        reported = message["result"]["status"]
                    elif "error" in message:
                        reported = "failed"
                    elif "step_id" not in message and message.get("status") in ("succeeded", "failed"):
                        terminal = message["status"]
        except (OSError, ValueError, HTTPException):
            pass
        finally:
            connection.close()
        return reported or terminal, None
