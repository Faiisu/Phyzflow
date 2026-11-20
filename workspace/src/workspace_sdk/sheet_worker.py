"""Start a sheet worker using its Workspace interpreter and Phyzflow's SDK."""

from importlib.metadata import PackageNotFoundError, distributions, version
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
from time import monotonic, sleep
import tomllib
import traceback


SHEET_WORKER_PROTOCOL_VERSION = 1


def _loaded_sdk_version(package_file: str) -> str | None:
    package = Path(package_file).resolve().parent
    if package.parent.name == "src":
        config = package.parent.parent / "pyproject.toml"
        if config.is_file():
            with config.open("rb") as source:
                return tomllib.load(source)["project"]["version"]
    installed = next(distributions(name="workspace-sdk", path=[str(package.parent)]), None)
    return installed.version if installed is not None else None


def _installed_sdk_version() -> str | None:
    try:
        return version("workspace-sdk")
    except PackageNotFoundError:
        return None


def _publish(target: Path, reply: dict) -> None:
    pending = target.with_suffix(".tmp")
    pending.write_text(json.dumps(reply), encoding="utf-8")
    pending.replace(target)


def _relative_location(root: Path, filename: str | None) -> str | None:
    if not filename:
        return None
    path = Path(filename)
    for base in (root, root.resolve()):
        if path.is_relative_to(base):
            return str(path.relative_to(base))
    resolved = path.resolve(strict=False)
    resolved_root = root.resolve()
    if resolved.is_relative_to(resolved_root):
        return str(resolved.relative_to(resolved_root))
    return str(path)


def _extract_location(error: BaseException, root: Path, *, include_tb: bool) -> tuple[str | None, int | None]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, SyntaxError):
            return _relative_location(root, current.filename), current.lineno
        current = current.__cause__
    if include_tb and error.__traceback__ is not None:
        frames = traceback.extract_tb(error.__traceback__)
        resolved_root = root.resolve()
        project_frames = [
            frame for frame in frames
            if frame.filename and not frame.filename.startswith("<")
            and Path(frame.filename).resolve(strict=False).is_relative_to(resolved_root)
        ]
        chosen = project_frames[-1] if project_frames else (frames[-1] if frames else None)
        if chosen is not None and chosen.filename:
            return _relative_location(root, chosen.filename), chosen.lineno
    return None, None


if __name__ == "__main__":
    sdk = spec_from_file_location("workspace_sdk", Path(__file__).with_name("__init__.py"))
    package = sys.modules["workspace_sdk"] = module_from_spec(sdk)
    sdk.loader.exec_module(package)

    if len(sys.argv) >= 3 and sys.argv[1] == "--check":
        from contextlib import redirect_stdout
        from workspace_sdk.project import ValidationError, load_project, workspace_format_notice
        from workspace_sdk.sheets import workspace_root_for_sheet

        target = Path(sys.argv[2])
        pending = target.with_suffix(".tmp")
        root = Path.cwd()
        try:
            workspace_format_notice(Path(sys.argv[3]) if len(sys.argv) > 3 else workspace_root_for_sheet(root))
            with redirect_stdout(sys.stderr), load_project(root, None) as flow:
                payload = {"valid": True, "steps": [step.id for step in flow.steps]}
        except ValidationError as error:
            file, line = _extract_location(error, root, include_tb=False)
            payload = {"valid": False, "stage": "validation", "error": str(error)}
            if file is not None:
                payload["file"] = file
            if line is not None:
                payload["line"] = line
        except Exception as error:
            file, line = _extract_location(error, root, include_tb=True)
            payload = {"valid": False, "stage": "import", "error": f"{type(error).__name__}: {error}"}
            if file is not None:
                payload["file"] = file
            if line is not None:
                payload["line"] = line
        pending.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
        pending.replace(target)
        raise SystemExit(0)

    if len(sys.argv) < 4 or sys.argv[1] != "--start":
        raise SystemExit("Expected --start and a startup file path.")

    startup = Path(sys.argv[2])
    _publish(startup, {
        "protocol_version": SHEET_WORKER_PROTOCOL_VERSION,
        "loaded_sdk_version": _loaded_sdk_version(package.__file__),
        "installed_sdk_version": _installed_sdk_version(),
    })
    approved = startup.with_name("approved")
    deadline = monotonic() + 10
    while not approved.is_file():
        if not startup.parent.is_dir() or monotonic() >= deadline:
            raise SystemExit("Sheet Worker startup was not approved.")
        sleep(.02)

    from workspace_sdk.workspace_ui import serve_flow

    ready = startup.with_name("ready.json")

    def publish(reply):
        if ready.parent.is_dir():
            _publish(ready, reply)

    serve_flow(
        Path.cwd(), json.loads(sys.argv[3]), publish,
        run_token=(sys.argv[4] or None) if len(sys.argv) > 4 else None,
        main=len(sys.argv) > 5 and sys.argv[5] == "1",
        workspace_root=Path(sys.argv[6]) if len(sys.argv) > 6 else None,
    )
