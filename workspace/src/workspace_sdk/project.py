from collections.abc import Iterator
from contextlib import contextmanager
from graphlib import CycleError, TopologicalSorter
from importlib.machinery import PathFinder, SourceFileLoader
import importlib.util
from inspect import signature
from pathlib import Path
import sys
import tomllib
from types import CodeType
from typing import Callable

from .flow import Flow, Input, Output, Step


class ValidationError(ValueError):
    """A project cannot be loaded or its declared flow cannot be run."""


def source_location(function: Callable) -> tuple[str | None, int | None]:
    code = getattr(function, "__code__", None)
    if isinstance(code, CodeType):
        return code.co_filename, code.co_firstlineno
    return None, None


def ordered_steps(flow: Flow) -> tuple[Step, ...]:
    steps = {step.id: step for step in flow.steps}
    if len(steps) != len(flow.steps):
        raise ValidationError("Flow step IDs must be unique.")
    graph = {
        step.id: tuple(
            reference.step_id for reference in step.arguments.values()
            if isinstance(reference, Output)
        )
        for step in flow.steps
    }
    for step_id, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in steps:
                raise ValidationError(f"{step_id} depends on unknown step {dependency!r}.")
    if flow.output.step_id not in steps:
        raise ValidationError(f"Unknown output step {flow.output.step_id!r}.")
    try:
        return tuple(steps[step_id] for step_id in TopologicalSorter(graph).static_order())
    except CycleError as error:
        cycle = " -> ".join(error.args[1])
        raise ValidationError(f"Dependency cycle: {cycle}.") from error


def validate_bindings(flow: Flow, steps: tuple[Step, ...]) -> None:
    for step in steps:
        for reference in step.arguments.values():
            if isinstance(reference, Input) and reference.name not in flow.inputs:
                raise ValidationError(f"{step.id} uses unknown flow input {reference.name!r}.")
            if not isinstance(reference, (Input, Output)):
                raise ValidationError(f"{step.id} arguments must refer to flow inputs or step outputs.")
        try:
            signature(step.function).bind(**step.arguments)
        except (TypeError, ValueError) as error:
            file, line = source_location(step.function)
            location = f" ({file}:{line})" if file else ""
            raise ValidationError(f"Invalid arguments for {step.id}{location}: {error}") from error


def check_source(root: Path, flow_path: Path) -> dict[Path, CodeType]:
    config_path = root / "pyproject.toml"
    sources = []
    if config_path.is_file():
        try:
            with config_path.open("rb") as config:
                tool = tomllib.load(config).get("tool", {})
            settings = tool.get("workspace", {}) if isinstance(tool, dict) else None
            if not isinstance(settings, dict):
                raise ValidationError(f"Source configuration: {config_path}: tool.workspace must be a table.")
            sources = settings.get("sources", [])
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValidationError(f"Source configuration: {config_path}: {error}") from error
        if not isinstance(sources, list) or not all(isinstance(item, str) for item in sources):
            raise ValidationError(f"Source configuration: {config_path}: sources must be an array of paths.")

    files = {flow_path}
    for entry in sources:
        source = (root / entry).resolve()
        if not source.is_relative_to(root):
            raise ValidationError(f"Source configuration: {config_path}: {entry!r} is outside the project.")
        if source.is_dir():
            files.update(source.rglob("*.py"))
        elif source.is_file() and source.suffix == ".py":
            files.add(source)
        else:
            raise ValidationError(f"Source configuration: {config_path}: {entry!r} is not a Python source path.")

    code = {}
    for path in sorted(files):
        path = path.resolve()
        if not path.resolve().is_relative_to(root):
            raise ValidationError(f"Source configuration: {path} is outside the project.")
        try:
            code[path] = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
        except SyntaxError as error:
            raise ValidationError(f"Syntax: {path}:{error.lineno}: {error.msg}") from error
        except OSError as error:
            raise ValidationError(f"Source: {path}: {error.strerror}") from error
    return code


class CheckedSourceLoader(SourceFileLoader):
    def __init__(self, name: str, path: str, code: CodeType):
        super().__init__(name, path)
        self.code = code

    def get_code(self, fullname: str) -> CodeType:
        return self.code


@contextmanager
def load_project(root: Path) -> Iterator[Flow]:
    root = root.resolve()
    path = root / "flow.py"
    code = check_source(root, path)

    class CheckedProjectFinder:
        def find_spec(self, fullname, search_path=None, target=None):
            spec = PathFinder.find_spec(fullname, search_path, target)
            if spec and isinstance(spec.loader, SourceFileLoader):
                checked = code.get(Path(spec.origin).resolve())
                if checked is not None:
                    # Use the checked source even when an older bytecode cache still matches.
                    spec.loader = CheckedSourceLoader(fullname, spec.origin, checked)
            return spec

    name = "_workspace_flow"
    loader = CheckedSourceLoader(name, str(path), code[path.resolve()])
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    finder = CheckedProjectFinder()
    sys.path.insert(0, str(root))
    sys.meta_path.insert(sys.meta_path.index(PathFinder), finder)
    sys.modules[name] = module
    try:
        loader.exec_module(module)
        flow = getattr(module, "flow", None)
        if not isinstance(flow, Flow):
            raise ValidationError(f"Entrypoint: {path} must declare a Workspace Flow named 'flow'.")
        try:
            steps = ordered_steps(flow)
            validate_bindings(flow, steps)
        except ValidationError as error:
            raise ValidationError(f"Flow: {path}: {error}") from error
        yield flow
    finally:
        del sys.modules[name]
        sys.meta_path.remove(finder)
        sys.path.remove(str(root))
