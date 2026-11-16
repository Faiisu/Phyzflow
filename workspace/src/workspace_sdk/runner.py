from dataclasses import dataclass
from datetime import datetime, timezone
import traceback
from typing import Any, Callable
from uuid import uuid4

from .flow import Flow, Input
from .project import ordered_steps, source_location


@dataclass(frozen=True)
class RunResult:
    id: str
    status: str
    output: Any


def run(
    flow: Flow,
    inputs: dict[str, Any],
    on_event: Callable[[dict[str, Any]], None] | None = None,
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

    emit("running")
    for step in steps:
        path, line = source_location(step.function)
        location = {"step_id": step.id, "file": path, "line": line}
        if any(getattr(ref, "step_id", None) in unsuccessful for ref in step.arguments.values()):
            unsuccessful.add(step.id)
            emit("skipped", **location)
            continue

        arguments = {
            name: inputs[ref.name] if isinstance(ref, Input) else outputs[ref.step_id]
            for name, ref in step.arguments.items()
        }
        emit("running", **location)
        try:
            outputs[step.id] = step.function(**arguments)
        except Exception as error:
            unsuccessful.add(step.id)
            trace = traceback.TracebackException.from_exception(error)
            frame = trace.stack[-1]
            emit(
                "failed", step_id=step.id, file=frame.filename, line=frame.lineno,
                error_type=type(error).__name__, traceback="".join(trace.format()),
            )
        else:
            emit("succeeded", **location)

    status = "failed" if unsuccessful else "succeeded"
    emit(status)
    return RunResult(run_id, status, outputs.get(flow.output.step_id))
