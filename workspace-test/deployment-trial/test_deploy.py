"""Exercise workspace deploy against a local Linux ARM64 Docker engine."""

import json
import os
from shutil import copytree, ignore_patterns
import socket
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
from urllib.parse import urlsplit

from smoke_test import post


TRIAL = Path(__file__).resolve().parent
SDK = TRIAL.parents[1] / "workspace"
CLI = Path(sys.executable).with_name("workspace")
CONTEXT = os.environ.get("WORKSPACE_DOCKER_CONTEXT", "default")


class DeployTests(unittest.TestCase):
    def docker(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", "--context", CONTEXT, *arguments], check=True,
            capture_output=True, text=True, timeout=30,
        )

    def deploy(self, project: Path = TRIAL, port: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [CLI, "deploy", "--sdk", SDK, "--context", CONTEXT, "--port", str(port)],
            cwd=project, capture_output=True, text=True, timeout=300,
        )

    def test_verified_build_serves_and_traces_failed_requests(self) -> None:
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        deployment = json.loads(result.stdout)
        self.addCleanup(self.docker, "stop", deployment["container_id"])

        self.assertEqual(
            self.docker("inspect", "--format", "{{.Image}}", deployment["container_id"]).stdout.strip(),
            deployment["build_id"],
        )
        self.assertEqual(
            post(deployment["url"], {"text": "Urgent: duplicate invoice charge"}),
            (200, {"category": "billing", "priority": "urgent"}),
        )
        secret = f"private-ticket-{uuid4().hex}"
        status, error = post(deployment["url"], {"text": f"Debug failure: {secret}"})
        self.assertEqual(status, 500)
        self.assertIsInstance(error, dict)
        assert isinstance(error, dict)
        self.assertEqual(error["error"], "Prediction failed.")
        url = urlsplit(deployment["url"])
        assert url.hostname is not None and url.port is not None
        with socket.create_connection((url.hostname, url.port), timeout=5) as connection:
            connection.sendall(f"bad request {secret}\r\n".encode())
            connection.recv(4096)

        logs = self.docker("logs", deployment["container_id"])
        diagnostic, = [
            json.loads(line) for line in logs.stderr.splitlines() if line.startswith("{")
        ]
        self.assertEqual(diagnostic["build_id"], deployment["build_id"])
        self.assertEqual(diagnostic["run_id"], error["run_id"])
        self.assertEqual(diagnostic["step_id"], "Predict")
        self.assertEqual(diagnostic["file"], "flow.py")
        self.assertIsInstance(diagnostic["line"], int)
        self.assertNotIn(secret, logs.stdout + logs.stderr + json.dumps(error))
        self.assertNotIn("traceback", diagnostic)


    def test_invalid_builds_never_publish_a_container(self) -> None:
        failures = (
            ("syntax", "flow.py", "\ndef broken(:\n", "Validation failed", "Syntax:"),
            ("import", "flow.py", "\nimport missing_deployment_module\n", "Validation failed", "missing_deployment_module"),
            ("smoke", "smoke_test.py", "\nraise SystemExit('Forced smoke-test rejection.')\n", "Smoke test failed", "Forced smoke-test rejection."),
        )
        for name, filename, change, phase, diagnostic in failures:
            with self.subTest(case=name), TemporaryDirectory() as temporary:
                project = Path(temporary) / "trial"
                copytree(TRIAL, project, ignore=ignore_patterns("__pycache__"))
                dockerfile = project / "Dockerfile"
                docker_source = dockerfile.read_text().replace("RUN workspace check && ", "RUN ")
                if name in ("syntax", "import"):
                    docker_source += '\nENTRYPOINT ["sh", "-c", "exit 0"]\n'
                dockerfile.write_text(docker_source)
                path = project / filename
                path.write_text(path.read_text() + change)
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    port = listener.getsockname()[1]
                result = self.deploy(project, port)
                if result.returncode == 0:
                    self.docker("stop", json.loads(result.stdout)["container_id"])
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(result.stdout, "")
                self.assertIn(phase, result.stderr)
                self.assertIn(diagnostic, result.stderr)
                with self.assertRaises(ConnectionRefusedError):
                    socket.create_connection(("127.0.0.1", port), timeout=1).close()


if __name__ == "__main__":
    unittest.main()
