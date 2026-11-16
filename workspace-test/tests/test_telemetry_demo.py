import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


DEMO = Path(__file__).resolve().parents[1] / "sheets-demo" / "flows" / "Telemetry Control Room"


class TelemetryDemoTests(unittest.TestCase):
    def test_scoring_failure_and_delivery_replay_through_cli(self):
        with TemporaryDirectory() as temporary:
            project = Path(temporary) / "telemetry"
            shutil.copytree(DEMO, project, ignore=shutil.ignore_patterns(".workspace", "__pycache__"))
            database = project / ".workspace" / "telemetry.sqlite3"

            def run(failure):
                inputs = {"config": {"events": 55, "queue_capacity": 1, "score_rounds": 10, "simulate": failure}}
                return subprocess.run(
                    [str(Path(sys.executable).with_name("workspace")), "run", json.dumps(inputs)],
                    cwd=project,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )

            scoring = run("score")
            self.assertNotEqual(scoring.returncode, 0)
            self.assertIn("Run Parallel Pipeline: failed", scoring.stderr)
            self.assertIn("Persist Batch: skipped", scoring.stderr)
            self.assertFalse(database.exists())

            interrupted = run("delivery")
            self.assertNotEqual(interrupted.returncode, 0)
            self.assertIn("Deliver Alerts: failed", interrupted.stderr)
            self.assertIn("Report: skipped", interrupted.stderr)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0], 55)
                self.assertEqual(connection.execute("SELECT delivered FROM outbox").fetchall(), [(0,)] * 3)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM local_alerts").fetchone()[0], 0)

            recovered = run("none")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            output = json.loads(recovered.stdout)
            self.assertTrue(output["storage"]["replayed"])
            self.assertEqual(output["events"]["duplicates_removed"], 5)
            self.assertEqual(output["events"]["scored"], 53)
            self.assertEqual(output["events"]["dead_letters"], 2)
            self.assertEqual(output["pipeline"]["recovered_after_retry"], 4)
            self.assertEqual(output["alerts"]["newly_delivered"], 3)

            replay = run("none")
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertEqual(json.loads(replay.stdout)["alerts"]["newly_delivered"], 0)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM local_alerts").fetchone()[0], 3)
