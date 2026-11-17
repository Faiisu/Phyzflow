from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from graphlib import CycleError, TopologicalSorter
from hashlib import sha256
from importlib.machinery import PathFinder, SourceFileLoader
import importlib.util
from inspect import signature
import json
from pathlib import Path
import sys
import tomllib
from types import CodeType
from typing import Callable

from .flow import Flow, Input, Output, Step
from .node_settings import NodeSetting, parse_setting_defaults
from .node_sources import node_settings_path, read_node_source


class ValidationError(ValueError):
    """A project cannot be loaded or its declared flow cannot be run."""


def read_project_settings(root: Path) -> dict:
    try:
        values = json.loads((root / "setting.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(values, dict):
        raise ValueError("setting.json must contain a JSON object.")
    return values


def source_location(function: Callable) -> tuple[str | None, int | None]:
    code = getattr(function, "__code__", None)
    if isinstance(code, CodeType):
        return code.co_filename, code.co_firstlineno
    return None, None


def relative_source(root: Path, filename: str | None) -> str | None:
    if filename:
        path = Path(filename)
        if path.is_relative_to(root):
            return str(path.relative_to(root))
    return filename


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
    if steps[flow.output.step_id].track_generator:
        raise ValidationError("A tracked generator must be consumed inside the flow.")
    try:
        return tuple(steps[step_id] for step_id in TopologicalSorter(graph).static_order())
    except CycleError as error:
        cycle = " -> ".join(error.args[1])
        raise ValidationError(f"Dependency cycle: {cycle}.") from error


def validate_bindings(flow: Flow, steps: tuple[Step, ...]) -> None:
    for step in steps:
        if not isinstance(step.settings, dict) or any(
            type(name) is not str or not 1 <= len(name) <= 64 or not isinstance(setting, NodeSetting)
            for name, setting in step.settings.items()
        ):
            raise ValidationError(f"{step.id} settings must map names to NodeSetting declarations.")
        if len(set(step.settings.values())) != len(step.settings):
            raise ValidationError(f"{step.id} must declare a separate NodeSetting for each setting name.")
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


def setting_files(root: Path, steps: tuple[Step, ...]) -> dict[Path, dict[str, list[NodeSetting]]]:
    files: dict[Path, dict[str, list[NodeSetting]]] = {}
    for step in steps:
        filename, _ = source_location(step.function)
        path = node_settings_path(root, filename)
        if path is not None:
            definitions = files.setdefault(path, {})
            for name, setting in step.settings.items():
                definitions.setdefault(name, []).append(setting)
    return files


def load_setting_defaults(
    root: Path, flow: Flow, loaded_sources: dict[Path, str] | None,
) -> Flow:
    defaults = {}
    for path, definitions in setting_files(root, flow.steps).items():
        try:
            if path.resolve() != path:
                raise ValueError("Use a regular settings file inside the node folder.")
            try:
                content = read_node_source(path)
            except FileNotFoundError:
                continue
            defaults[path] = parse_setting_defaults(content.decode("utf-8-sig"), definitions)
        except (OSError, ValueError, RuntimeError) as error:
            raise ValidationError(f"Node settings: {path}: {error}") from error
        if loaded_sources is not None:
            loaded_sources[path] = sha256(content).hexdigest()

    if not defaults:
        return flow
    steps = []
    for step in flow.steps:
        filename, _ = source_location(step.function)
        values = defaults.get(node_settings_path(root, filename), {})
        if values:
            step = replace(step, setting_defaults={
                **step.setting_defaults,
                **{name: setting.validate(values[name]) for name, setting in step.settings.items() if name in values},
            })
        steps.append(step)
    return replace(flow, steps=tuple(steps))


def check_source(root: Path, flow_path: Path) -> dict[Path, tuple[CodeType, str]]:
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
            data = path.read_bytes()
            code[path] = (
                compile(data, str(path), "exec", dont_inherit=True),
                sha256(data).hexdigest(),
            )
        except SyntaxError as error:
            raise ValidationError(f"Syntax: {path}:{error.lineno}: {error.msg}") from error
        except OSError as error:
            raise ValidationError(f"Source: {path}: {error.strerror}") from error
    return code


class CheckedSourceLoader(SourceFileLoader):
    def __init__(
        self, name: str, path: Path, checked: tuple[CodeType, str] | None,
        loaded_sources: dict[Path, str] | None,
    ):
        super().__init__(name, str(path))
        self.source_path = path
        self.checked = checked
        self.loaded_sources = loaded_sources

    def get_code(self, fullname: str) -> CodeType:
        if self.checked is None:
            try:
                source = self.get_data(self.path)
                code = self.source_to_code(source, self.path)
            except SyntaxError as error:
                raise ValidationError(f"Syntax: {self.source_path}:{error.lineno}: {error.msg}") from error
            digest = sha256(source).hexdigest()
        else:
            code, digest = self.checked
        if self.loaded_sources is not None:
            self.loaded_sources[self.source_path] = digest
        return code


@contextmanager
def load_project(root: Path, loaded_sources: dict[Path, str] | None = None) -> Iterator[Flow]:
    root = root.resolve()
    path = (root / "flow.py").resolve()
    code = check_source(root, path)
    environment = Path(sys.prefix).resolve()

    class CheckedProjectFinder:
        def find_spec(self, fullname, search_path=None, target=None):
            spec = PathFinder.find_spec(fullname, search_path, target)
            if spec and spec.origin and isinstance(spec.loader, SourceFileLoader):
                origin = Path(spec.origin).resolve()
                checked = code.get(origin)
                local_source = origin.is_relative_to(root) and not origin.is_relative_to(environment)
                if checked is not None or (loaded_sources is not None and local_source):
                    # Compile the source we fingerprint, bypassing cached bytecode.
                    spec.loader = CheckedSourceLoader(fullname, origin, checked, loaded_sources)
            return spec

    name = "_workspace_flow"
    loader = CheckedSourceLoader(name, path, code[path], loaded_sources)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    if spec is None:
        raise ValidationError(f"Entrypoint: could not load {path}.")
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
        yield load_setting_defaults(root, flow, loaded_sources)
    finally:
        del sys.modules[name]
        sys.meta_path.remove(finder)
        sys.path.remove(str(root))
