from hashlib import sha256
import json
from pathlib import Path
from shutil import copytree, ignore_patterns
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from test_release_manifest import BUILDER, WORKSPACE, command, snapshot


CHECK = WORKSPACE / "release" / "check_compatibility.py"


class ReleaseCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        ignore = ignore_patterns("build", "dist", "*.egg-info", "__pycache__", ".venv", ".DS_Store")
        copytree(WORKSPACE, self.root / "workspace", ignore=ignore)
        copytree(WORKSPACE.parent / "workspace-test" / "tests", self.root / "workspace-test" / "tests", ignore=ignore)
        for name in ("browser_support.py", "pyproject.toml"):
            source = WORKSPACE.parent / "workspace-test" / name
            (self.root / "workspace-test" / name).write_bytes(source.read_bytes())
        command("git", "init", "-q", cwd=self.root)

    def check_candidate(self):
        commit = snapshot(self.root)
        source = self.root / "workspace"
        wheel = Path(command(sys.executable, BUILDER, "--source", source, "--out-dir", self.root / "dist", cwd=self.root))
        report = self.root / "compatibility.json"
        result = subprocess.run(
            [sys.executable, CHECK, wheel, "--report", report, "--source", self.root],
            capture_output=True, text=True, cwd=self.root,
        )
        self.assertTrue(report.is_file(), f"{result.stdout}\n{result.stderr}")
        data = json.loads(report.read_text())
        self.assertEqual(data["commit"], commit)
        self.assertEqual(data["wheel_sha256"], sha256(wheel.read_bytes()).hexdigest())
        self.assertNotIn("published_at", data)
        return result, data

    def test_untested_advertised_worker_protocol_blocks_release(self):
        app = self.root / "workspace/src/workspace_sdk/workspace_ui/flow_sheet.py"
        app.write_text(app.read_text().replace(
            "SUPPORTED_SHEET_WORKER_PROTOCOL_VERSIONS = (1,)",
            "SUPPORTED_SHEET_WORKER_PROTOCOL_VERSIONS = (1, 99)",
        ))
        result, report = self.check_candidate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Untested compatibility combination", result.stderr)
        self.assertEqual(report["result"], "failed")
        self.assertIn({
            "interface": "sheet_worker_protocol", "version": 99, "combination": 99,
            "expected": "compatible", "tests": [], "result": "untested",
        }, report["cases"])
        if sys.platform == "darwin":
            output = self.root / "macos-dist"
            package = subprocess.run(
                ["sh", self.root / "workspace/release/macos/build.sh", output],
                cwd=self.root, capture_output=True, text=True,
            )
            self.assertNotEqual(package.returncode, 0)
            self.assertIn("Untested compatibility combination", package.stderr)
            self.assertEqual(list(output.iterdir()), [])

    def test_candidate_wheel_passes_the_compatibility_matrix(self):
        result, report = self.check_candidate()
        self.assertEqual(result.returncode, 0, f"{result.stdout}\n{result.stderr}")
        self.assertEqual(report["result"], "passed")
        self.assertEqual({case["interface"] for case in report["cases"]}, {
            "workspace_format", "sheet_worker_protocol", "dashboard_bridge",
        })
        for interface in ("workspace_format", "sheet_worker_protocol", "dashboard_bridge"):
            cases = [case for case in report["cases"] if case["interface"] == interface]
            self.assertEqual({case["expected"] for case in cases}, {"compatible", "rejected"})
            self.assertTrue(all(case["result"] == "passed" and case["tests"] for case in cases))

    def test_failing_advertised_workspace_format_blocks_release(self):
        project = self.root / "workspace/src/workspace_sdk/project.py"
        project.write_text(project.read_text().replace(
            "type(value) is not int or value != WORKSPACE_FORMAT_VERSION",
            "type(value) is not int or value == WORKSPACE_FORMAT_VERSION",
        ))
        result, report = self.check_candidate()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["declared"]["workspace_format"], 1)
        self.assertEqual(report["result"], "failed")
        supported = next(case for case in report["cases"] if case["interface"] == "workspace_format" and case["combination"] == 1)
        self.assertEqual(supported["expected"], "compatible")
        self.assertEqual(supported["result"], "failed")
        self.assertTrue(all(report["test_results"][test] == "failed" for test in supported["tests"]))
