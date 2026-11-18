#!/bin/sh
set -eu

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and try again.' >&2
    exit 1
fi

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
default_project=1
if [ "$#" -gt 0 ]; then
    case "$1" in
        -*)
            project_dir="$repo_dir/default-workspace"
            ;;
        *)
            default_project=0
            project_dir=$1
            shift
            ;;
    esac
else
    project_dir="$repo_dir/default-workspace"
fi

if [ "$default_project" -eq 1 ]; then
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
uv pip install --python "$python" --reinstall-package workspace-sdk "$repo_dir/workspace[mcp]"
workspace_cli="$project_dir/.venv/bin/workspace"
if [ "$default_project" -eq 1 ] && [ ! -f "$project_dir/pyproject.toml" ]; then
    "$workspace_cli" --project "$project_dir" init --with-main
fi
exec "$python" "$repo_dir/scripts/start_mcp_workspace.py" --project "$project_dir" "$@"
