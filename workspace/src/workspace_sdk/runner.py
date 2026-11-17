from collections.abc import Iterator
from contextvars import copy_context
from dataclasses import dataclass
from datetime import datetime, timezone
import sys
from time import perf_counter_ns
import traceback
from types import GeneratorType, ModuleType
from typing import Any, Callable
from uuid import uuid4

from .evidence import active_metrics, active_output, active_progress, active_reasons, capture_sample
from .flow import Flow, Input, Output, Step
from .node_settings import LiveSettings, SettingValue, active_checkpoint, active_settings
from .project import ordered_steps, source_location


class _UpstreamGeneratorError(RuntimeError):
    def __init__(self, producer: str):
        super().__init__(f"Generator from {producer!r} failed.")
        self.producer = producer


@dataclass(frozen=True)
class RunResult:
    id: str
    status: str
    output: Any


def summarize(value: Any) -> dict[str, Any]:
    value_type = type(value)
    tensor = getattr(sys.modules.get("torch"), "Tensor", None)
    parameter = getattr(sys.modules.get("torch.nn"), "Parameter", None)
    if value_type is tensor or value_type is parameter:
        metadata: dict[str, Any] = {"type": "tensor", "dtype": str(value.dtype), "device": str(value.device)}
        if not value.is_nested:
            metadata["shape"] = list(value.shape)
        return metadata
    if value_type is getattr(sys.modules.get("numpy"), "ndarray", None):
        return {"type": "ndarray", "shape": list(value.shape), "dtype": value.dtype.type.__name__}
    if value_type is getattr(sys.modules.get("pandas"), "DataFrame", None):
        return {"type": "DataFrame", "shape": list(value.shape)}
    if value_type in (str, int, float, bool, bytes, dict, list, tuple, type(None)):
        return {"type": value_type.__name__}
    return {"type": "object"}


def diagnostic_error_type(error: Exception) -> str:
    for cls in type(error).__mro__:
        scope = sys.modules.get(cls.__module__)
        names = cls.__qualname__.split(".")
        for name in names:
            if not isinstance(scope, (ModuleType, type)):
                break
            scope = vars(scope).get(name)
        if scope is cls:
            return names[-1]
    return "Exception"


def diagnostic_frames(trace: traceback.TracebackException) -> Iterator[dict[str, str | int | None]]:
    cause = trace.__cause__
    if cause is None and not trace.__suppress_context__:
        cause = trace.__context__
    if cause is not None:
        yield from diagnostic_frames(cause)
    for frame in trace.stack:
        yield {"file": frame.filename, "line": frame.lineno}
    for child in trace.exceptions or ():
        yield from diagnostic_frames(child)


def run(
    flow: Flow,
    inputs: dict[str, Any],
    on_event: Callable[[dict[str, Any]], None] | None = None,
    *,
    include_metadata: bool = False,
    live_metrics: bool = False,
    settings: dict[str, dict[str, SettingValue]] | None = None,
    live_settings: LiveSettings | None = None,
) -> RunResult:
    """Execute a flow and emit run and step events to the optional observer."""
    steps = ordered_steps(flow)
    missing = set(flow.inputs).difference(inputs)
    if missing:
        raise ValueError(f"Missing flow inputs: {', '.join(sorted(missing))}.")
    run_id = uuid4().hex
    outputs = {}
    unsuccessful = set()
    pending_generators: dict[str, Callable[[], None]] = {}

    def emit(status, **details):
        if on_event is not None:
            on_event({
                "run_id": run_id,
                "time": datetime.now(timezone.utc).isoformat(),
                "status": status,
                **details,
            })

    input_metadata = {}
    if include_metadata:
        input_metadata["input"] = {name: summarize(inputs[name]) for name in flow.inputs}
    if live_settings is not None:
        live_settings.start(run_id)
    emit("running", **input_metadata)

    def execute_step(step: Step) -> None:
        path, line = source_location(step.function)
        location: dict[str, str | int | float | None] = {"step_id": step.id, "file": path, "line": line}
        blocked_by = list(dict.fromkeys(
            ref.step_id for ref in step.arguments.values() if isinstance(ref, Output) and ref.step_id in unsuccessful
        ))
        if blocked_by:
            unsuccessful.add(step.id)
            emit("skipped", **location, blocked_by=blocked_by)
            return

        arguments = {
            name: inputs[ref.name] if isinstance(ref, Input) else outputs[ref.step_id]
            for name, ref in step.arguments.items()
        }
        argument_metadata = {}
        collect_samples = include_metadata and step.capture_samples is True
        if include_metadata:
            argument_metadata["input"] = {name: summarize(value) for name, value in arguments.items()}
        if collect_samples:
            argument_metadata["input_sample"] = capture_sample(arguments)
        values = {
            setting: (settings or {}).get(step.id, {}).get(name, setting.default)
            for name, setting in step.settings.items()
        }
        controlled = live_settings if any(setting.live for setting in values) else None
        if controlled is not None:
            controlled.start_node(step.id)

        def apply_pending():
            for command in controlled.take(step.id):
                values[step.settings[command["name"]]] = command["value"]
                emit("running", step_id=step.id, setting_change={**command, "status": "applied"})

        emit("running", **location, **argument_metadata)
        reasons: list[str] | None = [] if include_metadata else None
        metrics: dict[str, dict[str, int | float]] | None = {} if include_metadata else None
        def invoke(function, /, *args, **kwargs):
            reason_scope = active_reasons.set(reasons)
            metrics_scope = active_metrics.set(metrics)
            output_scope = active_output.set(
                (lambda preview: emit("running", step_id=step.id, output_preview=preview))
                if on_event is not None else None
            )
            progress_scope = active_progress.set(
                (lambda stage, values: emit("running", step_id=step.id, live_metrics={stage: values}))
                if live_metrics else None
            )
            settings_scope = active_settings.set(values or None)
            checkpoint_scope = active_checkpoint.set(apply_pending if controlled is not None else None)
            try:
                return function(*args, **kwargs)
            finally:
                active_reasons.reset(reason_scope)
                active_metrics.reset(metrics_scope)
                active_output.reset(output_scope)
                active_progress.reset(progress_scope)
                active_settings.reset(settings_scope)
                active_checkpoint.reset(checkpoint_scope)

        context = copy_context() if step.track_generator else None
        started = perf_counter_ns() if on_event is not None else None

        def duration() -> dict[str, float]:
            return {"duration_ms": (perf_counter_ns() - started) / 1_000_000} if started is not None else {}

        def finish(status: str, **details: Any) -> None:
            pending_generators.pop(step.id, None)
            if controlled is not None:
                for command in controlled.take(step.id, finish=True):
                    emit("running", step_id=step.id, setting_change={
                        **command, "status": "rejected", "message": "The node ended before applying this change.",
                    })
            if reasons:
                details["reasons"] = reasons.copy()
            if metrics:
                details["metrics"] = metrics.copy()
            emit(status, **location, **details)

        def complete(value: Any) -> None:
            elapsed = duration()
            metadata: dict[str, Any] = {"output": summarize(value)} if include_metadata else {}
            if collect_samples:
                metadata["output_sample"] = capture_sample(value)
            finish("succeeded", **elapsed, **metadata)

        def fail(error: Exception) -> None:
            elapsed = duration()
            unsuccessful.add(step.id)
            trace = traceback.TracebackException.from_exception(error)
            diagnostics: dict[str, Any] = {}
            if include_metadata:
                diagnostics["diagnostic_error_type"] = diagnostic_error_type(error)
                diagnostics["error_frames"] = list(diagnostic_frames(trace))
            if isinstance(error, _UpstreamGeneratorError):
                diagnostics["blocked_by"] = [error.producer]
            elif trace.stack:
                frame = trace.stack[-1]
                location.update(file=frame.filename, line=frame.lineno)
            finish("failed", error_type=type(error).__name__, traceback="".join(trace.format()),
                   **elapsed, **diagnostics)

        try:
            value = (context.run(invoke, step.function, **arguments) if context is not None
                     else invoke(step.function, **arguments))
            if context is not None and not isinstance(value, GeneratorType):
                raise TypeError("A node with track_generator=True must return a Python generator.")
        except Exception as error:
            fail(error)
            return
        if context is None:
            outputs[step.id] = value
            complete(value)
            return

        def close_generator() -> None:
            try:
                context.run(invoke, value.close)
            except Exception as error:
                fail(error)
            else:
                finish("stopped", **duration())

        def tracked_values() -> Iterator[Any]:
            while True:
                try:
                    item = context.run(invoke, next, value)
                except StopIteration:
                    complete(value)
                    return
                except Exception as error:
                    fail(error)
                    raise _UpstreamGeneratorError(step.id) from None
                yield item

        pending_generators[step.id] = close_generator
        outputs[step.id] = tracked_values()

    try:
        try:
            for step in steps:
                execute_step(step)
        finally:
            for close in list(pending_generators.values()):
                close()
    finally:
        if live_settings is not None:
            live_settings.stop()

    status = "failed" if unsuccessful else "succeeded"
    output = outputs.get(flow.output.step_id)
    metadata = {"output": summarize(output)} if include_metadata and status == "succeeded" else {}
    emit(status, **metadata)
    return RunResult(run_id, status, output)
