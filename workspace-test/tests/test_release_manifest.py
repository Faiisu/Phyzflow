import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory

WORKSPACE = Path(__file__).resolve().parents[2] / "workspace"
BUILDER = WORKSPACE / "release" / "build_wheel.py"
MODULES = {"application", "cli", "runner", "web_ui", "mcp"}


def command(*args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError(f"{args} exited with {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def snapshot(source):
    command("git", "add", ".", cwd=source)
    command("git", "-c", "user.name=Release Test", "-c", "user.email=release@example.invalid",
            "commit", "--no-gpg-sign", "-qm", "Test release", cwd=source)
    return command("git", "rev-parse", "HEAD", cwd=source)


def build_and_inspect(root, source, release, *builder_args):
    wheel = Path(command(sys.executable, BUILDER, "--source", source,
                         "--out-dir", root / "dist", *builder_args, cwd=root))
    environment = root / release
    command("uv", "venv", "--python", sys.executable, environment, cwd=root)
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    extension = ".exe" if os.name == "nt" else ""
    command("uv", "pip", "install", "--python", bin_dir / f"python{extension}", wheel, cwd=root)
    return json.loads(command(bin_dir / f"workspace{extension}", "version", "--json", cwd=root))


class ReleaseManifestTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.source = self.root / "workspace"
        copytree(
            WORKSPACE, self.source,
            ignore=ignore_patterns("build", "dist", "*.egg-info", "__pycache__", ".venv", ".DS_Store"),
        )
        command("git", "init", "-q", cwd=self.source)

    def test_installed_wheels_report_baseline_and_only_advance_the_changed_module(self):
        root, source = self.root, self.source
        pyproject = source / "pyproject.toml"
        config = pyproject.read_text(encoding="utf-8")
        baseline_version = tomllib.loads(config)["project"]["version"]
        baseline_commit = snapshot(source)
        baseline = build_and_inspect(root, source, baseline_version)

        self.assertEqual(baseline["version"], baseline_version)
        self.assertEqual(baseline["commit"], baseline_commit)
        self.assertEqual(set(baseline["modules"]), MODULES)
        for module in baseline["modules"].values():
            self.assertRegex(module["fingerprint"], r"^[a-f0-9]{64}$")
            self.assertEqual(module["last_changed_version"], baseline_version)

        previous = root / "previous.json"
        previous.write_text(json.dumps(baseline), encoding="utf-8")
        changed_version = f"{baseline_version}.post1"
        pyproject.write_text(
            config.replace(f'version = "{baseline_version}"', f'version = "{changed_version}"', 1), encoding="utf-8",
        )
        with (source / "src/workspace_sdk/static/workspace.js").open("a", encoding="utf-8") as asset:
            asset.write("\nwindow.phyzflowReleaseFixture = true;\n")
        changed_commit = snapshot(source)
        with (source / "src/workspace_sdk/runner.py").open("a", encoding="utf-8") as runner:
            runner.write("\nUNCOMMITTED_FIXTURE = True\n")

        changed = build_and_inspect(root, source, changed_version, "--previous-manifest", previous)

        self.assertEqual(changed["version"], changed_version)
        self.assertEqual(changed["commit"], changed_commit)
        self.assertEqual(set(changed["modules"]), MODULES)
        for name in MODULES - {"web_ui"}:
            self.assertEqual(changed["modules"][name], baseline["modules"][name])
        self.assertNotEqual(changed["modules"]["web_ui"]["fingerprint"], baseline["modules"]["web_ui"]["fingerprint"])
        self.assertEqual(changed["modules"]["web_ui"]["last_changed_version"], changed_version)

    def test_malformed_previous_manifest_prevents_building_a_release_wheel(self):
        root, source = self.root, self.source
        snapshot(source)
        previous = root / "previous.json"
        previous.write_text(json.dumps({
            "version": "1.0.0",
            "modules": {name: {"fingerprint": "a" * 64, "last_changed_version": "1.0.0"} for name in MODULES},
        }), encoding="utf-8")
        rejected_output = root / "rejected"
        result = subprocess.run(
            [sys.executable, BUILDER, "--source", source, "--out-dir", rejected_output,
             "--previous-manifest", previous],
            capture_output=True, text=True, cwd=root, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid previous release manifest", result.stderr)
        self.assertEqual(list(rejected_output.glob("*.whl")), [])
