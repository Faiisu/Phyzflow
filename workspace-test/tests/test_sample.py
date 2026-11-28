import json
import subprocess
import sys
import unittest
from pathlib import Path

from flow import flow
from ticket_demo.entrypoint import prepare
from workspace_sdk import run

PROJECT = Path(__file__).resolve().parents[1]


class SampleTests(unittest.TestCase):
    def test_python_and_cli_return_the_expected_output(self):
        sample = json.loads((PROJECT / "sample.json").read_text())
        self.assertEqual(prepare("  EXAMPLE  "), "example")
        events = []
        result = run(flow, sample["input"], events.append)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.output, sample["output"])
        self.assertEqual({event["run_id"] for event in events}, {result.id})

        steps = [event for event in events if "step_id" in event]
        self.assertEqual(
            [event["step_id"] for event in steps if event["status"] == "succeeded"],
            ["Prepare", "Predict", "Decide"],
        )
        self.assertTrue(all(event["time"] and event["file"] and event["line"] for event in steps))

        cli = Path(sys.executable).with_name("workspace")
        check = subprocess.run([cli, "check"], cwd=PROJECT, capture_output=True, text=True, check=False)
        self.assertEqual(check.returncode, 0, check.stderr)
        success = subprocess.run(
            [cli, "run", json.dumps(sample["input"])], cwd=PROJECT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(json.loads(success.stdout), sample["output"])

        failure = subprocess.run(
            [cli, "run", json.dumps(sample["failure_input"])], cwd=PROJECT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(failure.returncode, 1)
        self.assertIn("Predict: failed", failure.stderr)
        self.assertIn("prediction.py:3", failure.stderr)
        self.assertIn("Decide: skipped", failure.stderr)
