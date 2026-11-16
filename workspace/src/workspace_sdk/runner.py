from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
import sys
from time import perf_counter_ns
import traceback
from types import ModuleType
from typing import Any, Callable
from uuid import uuid4

from .evidence import active_reasons, capture_sample
from .flow import Flow, Input
from .project import ordered_steps, source_location


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
) -> RunResult:
    """Execute a flow and emit run and step events to the optional observer."""
    steps = ordered_steps(flow)
    missing = set(flow.inputs).difference(inputs)
    if missing:
        raise ValueError(f"Missing flow inputs: {', '.join(sorted(missing))}.")
    run_id = uuid4().hex
    outputs = {}
    unsuccessful = set()

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
    emit("running", **input_metadata)
    for step in steps:
        path, line = source_location(step.function)
        location: dict[str, str | int | float | None] = {"step_id": step.id, "file": path, "line": line}
        if any(getattr(ref, "step_id", None) in unsuccessful for ref in step.arguments.values()):
            unsuccessful.add(step.id)
            emit("skipped", **location)
            continue

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
        emit("running", **location, **argument_metadata)
        reasons: list[str] | None = [] if include_metadata else None
        reason_scope = active_reasons.set(reasons)
        started = perf_counter_ns() if on_event is not None else None
        try:
            try:
                outputs[step.id] = step.function(**arguments)
            finally:
                if started is not None:
                    location["duration_ms"] = (perf_counter_ns() - started) / 1_000_000
                active_reasons.reset(reason_scope)
        except Exception as error:
            unsuccessful.add(step.id)
            trace = traceback.TracebackException.from_exception(error)
            frame = trace.stack[-1]
            diagnostics = {}
            if include_metadata:
                diagnostics["diagnostic_error_type"] = diagnostic_error_type(error)
                diagnostics["error_frames"] = list(diagnostic_frames(trace))
            if reasons:
                diagnostics["reasons"] = reasons.copy()
            location.update(file=frame.filename, line=frame.lineno)
            emit(
                "failed", **location,
                error_type=type(error).__name__, traceback="".join(trace.format()),
                **diagnostics,
            )
        else:
            metadata: dict[str, Any] = {"output": summarize(outputs[step.id])} if include_metadata else {}
            if collect_samples:
                metadata["output_sample"] = capture_sample(outputs[step.id])
            if reasons:
                metadata["reasons"] = reasons.copy()
            emit("succeeded", **location, **metadata)

    status = "failed" if unsuccessful else "succeeded"
    output = outputs.get(flow.output.step_id)
    metadata = {"output": summarize(output)} if include_metadata and status == "succeeded" else {}
    emit(status, **metadata)
    return RunResult(run_id, status, output)
