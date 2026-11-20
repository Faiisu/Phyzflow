#!/bin/sh
set -eu
source_dir="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)"
launcher="$source_dir/release/macos"
output="${1:-$source_dir/dist}"
mkdir -p "$output"
output="$(CDPATH= cd -- "$output" && pwd -P)"
staging="$(mktemp -d "$output/.workspace-release.XXXXXX")"
trap 'rm -rf "$staging"' EXIT

release_python() {
    uv run --no-project --python 3.12 python -I "$@"
}

set -- --out-dir "$staging"
if [ -n "${PREVIOUS_RELEASE_MANIFEST:-}" ]; then
    set -- "$@" --previous-manifest "$PREVIOUS_RELEASE_MANIFEST"
else
    set -- "$@" --previous-macos-output "$output"
fi
wheel="$(release_python "$source_dir/release/build_wheel.py" "$@")"
report="$staging/compatibility.json"
if ! release_python "$source_dir/release/check_compatibility.py" "$wheel" --report "$report" >&2; then
    if [ -f "$report" ]; then cat "$report" >&2; fi
    exit 1
fi
version="${wheel##*/}"
version="${version#workspace_sdk-}"
version="${version%%-*}"
app="$staging/Workspace.app"
/usr/bin/osacompile -o "$app" "$launcher/Workspace.applescript"
info="$app/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleIdentifier -string local.workspace-sdk.launcher "$info"
/usr/bin/plutil -replace CFBundleShortVersionString -string "$version" "$info"
/usr/bin/plutil -replace CFBundleDocumentTypes -json '[{"CFBundleTypeRole":"Viewer","LSItemContentTypes":["public.folder"]}]' "$info"
resources="$app/Contents/Resources"
cp "$wheel" "$launcher/launch.py" "$resources/"
install -m 755 "$launcher/open-workspace" "$resources/open-workspace"
release_python - "$report" "$resources/${wheel##*/}" <<'PY'
from hashlib import sha256
import json
from pathlib import Path
import sys

report_path, wheel = map(Path, sys.argv[1:])
report = json.loads(report_path.read_text(encoding="utf-8"))
if report["result"] != "passed" or sha256(wheel.read_bytes()).hexdigest() != report["wheel_sha256"]:
    sys.exit("The packaged wheel differs from the verified candidate.")
wheel.with_name("phyzflow-compatibility.json").write_text(
    json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8",
)
PY
/usr/bin/codesign --force --sign - "$app"
/usr/bin/codesign --verify "$app"

release="$output/Workspace-$version-macos-$(uname -m).zip"
/usr/bin/ditto -c -k --keepParent "$app" "$staging/app.zip"
release_python - "$report" "$staging/app.zip" "$release" <<'PYTHON'
from datetime import UTC, datetime
from hashlib import file_digest
import json
from pathlib import Path
import sys

report_path, staged, release = map(Path, sys.argv[1:])
report = json.loads(report_path.read_text(encoding="utf-8"))
with staged.open("rb") as artifact:
    report["artifact_sha256"] = file_digest(artifact, "sha256").hexdigest()
staged.replace(release)
report["published_at"] = datetime.now(UTC).isoformat()
report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
report_path.replace(release.with_suffix(".zip.compatibility.json"))
PYTHON
printf '%s\n' "$release"
