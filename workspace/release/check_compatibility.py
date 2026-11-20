import argparse
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
from shutil import copy2
import subprocess
import sys
from tarfile import open as tar_open
from tempfile import TemporaryDirectory
import tomllib


WORKSPACE_CLI = "test_validation.ValidationTests.test_workspace_format_is_checked_before_flow_import"
WORKSPACE_APP = "test_application_add_workspace.AddWorkspaceTests.test_workspace_format_controls_whether_application_opens_sheets"
WORKSPACE_LEGACY = "test_validation.ValidationTests.test_unspecified_format_keeps_existing_source_validation"
WORKER = "test_application_worker_compatibility.ApplicationWorkerCompatibilityTests."
WORKER_COMPATIBLE = (
    WORKER + "test_supported_worker_opens_and_reports_sdk_when_workspace_has_no_distribution",
    WORKER + "test_worker_loads_application_sdk_even_when_workspace_has_a_different_distribution",
)
WORKER_REJECTED = WORKER + "test_unsupported_or_missing_protocol_is_diagnosed_before_loading_project"
BRIDGE = "test_dashboards.DashboardTests.test_dashboard_bridge_compatibility_is_local_to_each_panel"

CASES = (
    ("workspace_format", 1, 1, "compatible", (WORKSPACE_CLI, WORKSPACE_APP)),
    ("workspace_format", 1, 2, "rejected", (WORKSPACE_CLI, WORKSPACE_APP)),
    ("workspace_format", 1, None, "compatible", (WORKSPACE_LEGACY, WORKSPACE_APP)),
    ("sheet_worker_protocol", 1, 1, "compatible", WORKER_COMPATIBLE),
    ("sheet_worker_protocol", 1, 99, "rejected", (WORKER_REJECTED,)),
    ("sheet_worker_protocol", 1, None, "rejected", (WORKER_REJECTED,)),
    ("dashboard_bridge", 1, [2, 1], "compatible", (BRIDGE,)),
    ("dashboard_bridge", 1, [2, 3], "rejected", (BRIDGE,)),
    ("dashboard_bridge", 1, None, "compatible", (BRIDGE,)),
)

READ_DECLARATIONS = """
import json
from workspace_sdk.dashboard import DASHBOARD_BRIDGE_VERSION
from workspace_sdk.project import WORKSPACE_FORMAT_VERSION
from workspace_sdk.sheet_worker import SHEET_WORKER_PROTOCOL_VERSION
from workspace_sdk.workspace_ui.flow_sheet import SUPPORTED_SHEET_WORKER_PROTOCOL_VERSIONS

print(json.dumps({
    'workspace_format': WORKSPACE_FORMAT_VERSION,
    'sheet_worker_protocol': SHEET_WORKER_PROTOCOL_VERSION,
    'application_supported_worker_protocols': SUPPORTED_SHEET_WORKER_PROTOCOL_VERSIONS,
    'dashboard_bridge': DASHBOARD_BRIDGE_VERSION,
}))
"""
RUN_TEST = """
from pathlib import Path
import sys
import unittest

root, name = sys.argv[1:]
sys.path.extend((root, str(Path(root) / 'tests')))
suite = unittest.defaultTestLoader.loadTestsFromName(name)
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not (result.testsRun == 1 and result.wasSuccessful() and not result.skipped and not result.expectedFailures))
"""


def compatibility_cases(python, cli, root, source, wheel, env, report):
    def run(args, **options):
        return subprocess.run(args, cwd=root, env=env, **options)

    run(["uv", "venv", "--no-project", "--python", sys.executable, python.parent.parent], check=True)
    run(["uv", "pip", "install", "--python", python, f"{wheel}[mcp]"], check=True)
    manifest = json.loads(run([cli, "version", "--json"], check=True, stdout=subprocess.PIPE).stdout)
    commit = manifest["commit"]
    if not re.fullmatch(r"[a-f0-9]{40}(?:[a-f0-9]{24})?", commit):
        raise ValueError("Expected a full Git commit ID in the wheel manifest.")
    report.update(version=manifest["version"], commit=commit)
    declared = json.loads(run([python, "-I", "-c", READ_DECLARATIONS], check=True, stdout=subprocess.PIPE).stdout)
    report["declared"] = declared

    worker_versions = declared["application_supported_worker_protocols"]
    if not isinstance(worker_versions, list) or not worker_versions:
        raise ValueError("The Application must declare at least one supported Sheet Worker protocol.")
    advertised = {
        "workspace_format": [declared["workspace_format"]],
        "sheet_worker_protocol": worker_versions,
        "dashboard_bridge": [declared["dashboard_bridge"]],
    }
    for interface, version, combination, expected, tests in CASES:
        report["cases"].append({
            "interface": interface, "version": version, "combination": combination,
            "expected": expected, "tests": tests, "result": "not_run",
        })

    covered = {(interface, version) for interface, version, _, expected, tests in CASES if expected == "compatible" and tests}
    if any(type(version) is not int for versions in advertised.values() for version in versions):
        raise ValueError("Advertised compatibility versions must be integers.")
    missing = {
        (interface, version)
        for interface, versions in advertised.items()
        for version in versions
        if (interface, version) not in covered
    }
    worker = declared["sheet_worker_protocol"]
    if type(worker) is not int:
        raise ValueError("The Sheet Worker protocol version must be an integer.")
    if worker not in worker_versions:
        missing.add(("sheet_worker_protocol", worker))
    if missing:
        for interface, version in sorted(missing):
            report["cases"].append({
                "interface": interface, "version": version, "combination": version,
                "expected": "compatible", "tests": [], "result": "untested",
            })
            print(f"Untested compatibility combination: {interface} {version!r}", file=sys.stderr)
        return False

    archive = run([
        "git", "-C", source, "archive", commit, "workspace-test/tests", "workspace-test/browser_support.py",
        "workspace-test/pyproject.toml",
    ], check=True, stdout=subprocess.PIPE).stdout
    with tar_open(fileobj=BytesIO(archive)) as files:
        files.extractall(root, filter="data")
    test_root = root / "workspace-test"
    config = tomllib.loads((test_root / "pyproject.toml").read_text(encoding="utf-8"))
    playwright, = (dep for dep in config["project"]["optional-dependencies"]["verification"] if dep.startswith("playwright=="))
    run(["uv", "pip", "install", "--python", python, playwright], check=True)

    results = report["test_results"]
    for test in dict.fromkeys(test for case in report["cases"] for test in case["tests"]):
        result = run([python, "-I", "-c", RUN_TEST, test_root, test])
        results[test] = "failed" if result.returncode else "passed"
    for case in report["cases"]:
        case["result"] = "passed" if all(results[test] == "passed" for test in case["tests"]) else "failed"
    return all(result == "passed" for result in results.values())


def main():
    parser = argparse.ArgumentParser(description="Verify compatibility against an installed release wheel.")
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2], help="Git repository with the candidate commit.")
    args = parser.parse_args()
    if args.report.resolve() == args.wheel.resolve():
        parser.error("The report path must differ from the wheel path.")

    report = {"wheel": args.wheel.name, "result": "failed", "cases": [], "test_results": {}}
    env = {name: value for name, value in os.environ.items() if not name.startswith("PYTHON")}
    try:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheel = Path(copy2(args.wheel, root))
            report["wheel_sha256"] = sha256(wheel.read_bytes()).hexdigest()
            bin_dir = root / (".venv/Scripts" if os.name == "nt" else ".venv/bin")
            suffix = ".exe" if os.name == "nt" else ""
            python, cli = (bin_dir / (name + suffix) for name in ("python", "workspace"))
            if compatibility_cases(python, cli, root, args.source.resolve(), wheel, env, report):
                report["result"] = "passed"
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        report["error"] = str(error)
        print(f"Compatibility check: {error}", file=sys.stderr)

    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=report_path.parent) as temporary:
        staged = Path(temporary) / report_path.name
        staged.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        staged.replace(report_path)
    print(f"Compatibility check {report['result']}: {report_path}")
    return int(report["result"] != "passed")


if __name__ == "__main__":
    sys.exit(main())
