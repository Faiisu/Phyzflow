#!/bin/sh
set -eu

if [ "$#" -gt 1 ]; then
    printf '%s\n' 'Usage: run-workspace.sh [project-directory]' >&2
    exit 2
fi
if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and try again.' >&2
    exit 1
fi

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
project_dir=${1:-"$repo_dir/default-workspace"}
if [ -z "${1:-}" ]; then
    mkdir -p "$project_dir"
fi
if [ ! -d "$project_dir" ]; then
    printf 'Workspace directory does not exist: %s\n' "$project_dir" >&2
    exit 1
fi
project_dir=$(CDPATH= cd -- "$project_dir" && pwd -P)
python="$project_dir/.venv/bin/python"
if [ ! -x "$python" ]; then
    uv venv --allow-existing --python 3.12 "$project_dir/.venv"
fi
uv pip install --python "$python" --reinstall-package workspace-sdk "$repo_dir/workspace"
workspace_cli="$project_dir/.venv/bin/workspace"
if [ -z "${1:-}" ] && [ ! -f "$project_dir/pyproject.toml" ]; then
    "$workspace_cli" --project "$project_dir" init --with-main
fi
exec "$workspace_cli" --project "$project_dir" ui --prompt
