import argparse
from base64 import urlsafe_b64encode
from copy import copy
import csv
from email.parser import BytesParser
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
import subprocess
import sys
from tarfile import open as tar_open
from tempfile import TemporaryDirectory
from zipfile import ZipFile


MODULE_FILES = {
    "cli": {"cli.py", "deploy.py"},
    "runner": {
        "__init__.py", "console.py", "evidence.py", "execution.py", "flow.py", "history.py",
        "node_settings.py", "node_sources.py", "project.py", "runner.py",
    },
    "web_ui": {"dashboard.py", "static", "ui_settings.py", "web.py", "workspace_ui"},
    "mcp": {"agent_mcp.py", "application_mcp", "mcp_flow_guidance.py"},
}
FILE_MODULE = {name: module for module, names in MODULE_FILES.items() for name in names}
MODULES = ("application", *MODULE_FILES)
MANIFEST_NAME = "phyzflow-release.json"


def read_previous_modules(path):
    if path is None:
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        previous = manifest["modules"]
        if not re.fullmatch(r"[a-f0-9]{40}(?:[a-f0-9]{24})?", manifest["commit"]):
            raise ValueError("Expected a full Git commit ID.")
        if not isinstance(manifest["version"], str) or not manifest["version"]:
            raise ValueError("Expected a release version.")
        if isinstance(previous, dict) and set(previous) == set(MODULES) and all(
            re.fullmatch(r"[a-f0-9]{64}", module["fingerprint"]) and
            isinstance(module["last_changed_version"], str) and module["last_changed_version"]
            for module in previous.values()
        ):
            return previous
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError(f"Invalid previous release manifest {path}: {error}") from error
    raise ValueError(f"Invalid previous release manifest {path}: expected all module fingerprints and release versions.")


def write_release_wheel(raw_wheel, release_wheel, commit, previous):
    with ZipFile(raw_wheel) as raw:
        record_name, = (name for name in raw.namelist() if name.endswith(".dist-info/RECORD"))
        dist_info = record_name.rsplit("/", 1)[0]
        version = BytesParser().parsebytes(raw.read(f"{dist_info}/METADATA"))["Version"]
        fingerprints = {module: sha256() for module in MODULES}
        for name in sorted(raw.namelist()):
            if name.endswith("/") or not name.startswith("workspace_sdk/"):
                continue
            package_member = name.split("/", 2)[1]
            digest = fingerprints[FILE_MODULE.get(package_member, "application")]
            digest.update(name.encode("utf-8") + b"\0" + sha256(raw.read(name)).digest())
        modules = {}
        for module, digest in fingerprints.items():
            fingerprint = digest.hexdigest()
            earlier = previous.get(module)
            changed_version = earlier["last_changed_version"] if earlier and earlier["fingerprint"] == fingerprint else version
            modules[module] = {"fingerprint": fingerprint, "last_changed_version": changed_version}
        manifest = {"version": version, "commit": commit, "modules": modules}
        manifest_name = f"{dist_info}/{MANIFEST_NAME}"
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        record = StringIO(raw.read(record_name).decode("utf-8"), newline="")
        record.seek(0, 2)
        checksum = urlsafe_b64encode(sha256(manifest_bytes).digest()).rstrip(b"=").decode("ascii")
        csv.writer(record).writerow((manifest_name, f"sha256={checksum}", len(manifest_bytes)))

        record_info = raw.getinfo(record_name)
        manifest_info = copy(record_info)
        manifest_info.filename = manifest_name
        with ZipFile(release_wheel, "w") as release:
            for info in raw.infolist():
                if info.filename != record_name:
                    release.writestr(info, raw.read(info))
            release.writestr(manifest_info, manifest_bytes)
            release.writestr(record_info, record.getvalue().encode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Build a Phyzflow release wheel from committed source.")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1], help="SDK source directory.")
    parser.add_argument("--out-dir", type=Path, required=True, help="Output directory for the release wheel.")
    parser.add_argument("--previous-manifest", type=Path, help="Manifest from the preceding published release.")
    args = parser.parse_args()
    try:
        previous = read_previous_modules(args.previous_manifest)
    except ValueError as error:
        parser.error(str(error))
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    git = ["git", "-C", str(args.source)]
    commit = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    source_archive = subprocess.check_output([*git, "archive", commit])
    with TemporaryDirectory(dir=output) as directory:
        staging = Path(directory)
        snapshot = staging / "source"
        with tar_open(fileobj=BytesIO(source_archive)) as archive:
            archive.extractall(snapshot, filter="data")
        subprocess.run(["uv", "build", "--wheel", "--out-dir", staging, snapshot], check=True, stdout=sys.stderr)
        raw_wheel, = staging.glob("workspace_sdk-*.whl")
        candidate = snapshot / raw_wheel.name
        write_release_wheel(raw_wheel, candidate, commit, previous)
        print(candidate.replace(output / raw_wheel.name))


if __name__ == "__main__":
    main()
