#!/bin/sh
set -eu

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and try again.' >&2
    exit 1
fi

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
venv_dir="$repo_dir/.venv"
python="$venv_dir/bin/python"

if [ ! -x "$python" ]; then
    uv venv --allow-existing --python 3.12 "$venv_dir"
fi

uv pip install --python "$python" --reinstall-package workspace-sdk "$repo_dir/workspace[mcp]"
exec "$venv_dir/bin/workspace" app "$@"
