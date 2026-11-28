import json
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
from zipfile import ZipFile

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
            capture_output=True, text=True, cwd=self.root, check=False,
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
                cwd=self.root, capture_output=True, text=True, check=False,
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

    def test_worker_combinations_keep_independent_results_and_require_matching_fixtures(self):
        fixture = self.root / "workspace-test/tests/test_application_worker_compatibility.py"
        fixture.write_text(fixture.read_text().replace("for protocol in (99, None):", "for protocol in (98, None):"))
        result, report = self.check_candidate()
        self.assertNotEqual(result.returncode, 0)
        cases = {
            case["combination"]: case["result"]
            for case in report["cases"] if case["interface"] == "sheet_worker_protocol"
        }
        self.assertEqual(cases, {1: "passed", 99: "untested", None: "passed"})

    @unittest.skipUnless(sys.platform == "darwin", "macOS packaging requires Apple tools")
    def test_macos_publication_records_the_zip_and_preserves_previous_module_versions(self):
        output = self.root / "macos-dist"
        builder = self.root / "workspace/release/macos/build.sh"

        def package():
            before = datetime.now(UTC)
            artifact = Path(command("sh", builder, output, cwd=self.root))
            after = datetime.now(UTC)
            published = json.loads(artifact.with_suffix(".zip.compatibility.json").read_text())
            self.assertEqual(published["artifact_sha256"], sha256(artifact.read_bytes()).hexdigest())
            self.assertLessEqual(before, datetime.fromisoformat(published["published_at"]))
            self.assertLessEqual(datetime.fromisoformat(published["published_at"]), after)
            self.assertGreaterEqual(datetime.fromisoformat(published["published_at"]).timestamp(), artifact.stat().st_mtime)
            with ZipFile(artifact) as archive:
                signed = json.loads(archive.read("Workspace.app/Contents/Resources/phyzflow-compatibility.json"))
                self.assertNotIn("published_at", signed)
                self.assertEqual(signed["wheel_sha256"], published["wheel_sha256"])
                self.assertEqual(signed["result"], "passed")
                wheel = archive.read("Workspace.app/Contents/Resources/" + signed["wheel"])
                self.assertEqual(sha256(wheel).hexdigest(), published["wheel_sha256"])
            with ZipFile(BytesIO(wheel)) as archive:
                name, = (name for name in archive.namelist() if name.endswith("/phyzflow-release.json"))
                return json.loads(archive.read(name))

        snapshot(self.root)
        previous = package()
        config = self.root / "workspace/pyproject.toml"
        version = previous["version"]
        next_version = version.rsplit(".", 1)[0] + "." + str(int(version.rsplit(".", 1)[1]) + 1)
        config.write_text(config.read_text().replace(f'version = "{version}"', f'version = "{next_version}"', 1))
        sdk = self.root / "workspace/src/workspace_sdk"
        with (sdk / "cli.py").open("a") as cli:
            cli.write("\n\nRELEASE_HISTORY_TEST = True\n")
        snapshot(self.root)
        current = package()
        self.assertEqual(current["version"], next_version)
        self.assertEqual(current["modules"]["cli"]["last_changed_version"], next_version)
        self.assertNotEqual(current["modules"]["cli"]["fingerprint"], previous["modules"]["cli"]["fingerprint"])
        for module in ("application", "mcp", "runner", "web_ui"):
            self.assertEqual(current["modules"][module], previous["modules"][module])

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
