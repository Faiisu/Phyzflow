from collections.abc import Iterator
from contextvars import copy_context
from dataclasses import dataclass
from datetime import datetime, timezone
from graphlib import TopologicalSorter
import sys
from time import perf_counter_ns
import traceback
from types import GeneratorType, ModuleType
from typing import Any, Callable
from uuid import uuid4

from .evidence import active_frame, active_metrics, active_output, active_progress, active_reasons, capture_sample
from .flow import Flow, Input, LoopOutput, Output, Step
from .node_settings import LiveSettings, SettingValue, active_checkpoint, active_settings
from .project import DEFAULT_MAX_LOOP_ITERATIONS, ordered_steps, source_location


class LoopLimitError(RuntimeError):
    pass


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


def _strongly_connected_components(
    node_ids: list[str], adjacency: dict[str, list[str]],
) -> list[list[str]]:
    index_counter = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def strongconnect(node_id: str) -> None:
        nonlocal index_counter
        indices[node_id] = index_counter
        lowlink[node_id] = index_counter
        index_counter += 1
        stack.append(node_id)
        on_stack.add(node_id)

        for neighbor in adjacency.get(node_id, ()):
            if neighbor not in indices:
                strongconnect(neighbor)
                lowlink[node_id] = min(lowlink[node_id], lowlink[neighbor])
            elif neighbor in on_stack:
                lowlink[node_id] = min(lowlink[node_id], indices[neighbor])

        if lowlink[node_id] == indices[node_id]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node_id:
                    break
            components.append(component)

    for node_id in node_ids:
        if node_id not in indices:
            strongconnect(node_id)

    position = {node_id: idx for idx, node_id in enumerate(node_ids)}
    components.sort(key=lambda comp: min(position[node_id] for node_id in comp))
    component_of = {node_id: idx for idx, comp in enumerate(components) for node_id in comp}
    dependencies: dict[int, list[int]] = {idx: [] for idx in range(len(components))}
    for source, neighbors in adjacency.items():
        source_component = component_of[source]
        for target in neighbors:
            target_component = component_of[target]
            if source_component != target_component:
                dependencies[target_component].append(source_component)
    return [components[idx] for idx in TopologicalSorter(dependencies).static_order()]


def run(
    flow: Flow,
    inputs: dict[str, Any],
    on_event: Callable[[dict[str, Any]], None] | None = None,
    *,
    include_metadata: bool = False,
    live_metrics: bool = False,
    live_frames: bool = False,
    settings: dict[str, dict[str, SettingValue]] | None = None,
    live_settings: LiveSettings | None = None,
    run_id: str | None = None,
) -> RunResult:
    """Execute a flow and emit run and step events to the optional observer."""
    steps = ordered_steps(flow)
    missing = set(flow.inputs).difference(inputs)
    if missing:
        raise ValueError(f"Missing flow inputs: {', '.join(sorted(missing))}.")
    run_id = run_id or uuid4().hex
    outputs = {}
    unsuccessful = set()
    loop_blockers: dict[str, str] = {}
    loop_values: dict[tuple[str, str], Any] = {}
    step_totals_ns: dict[str, int] = {}
    step_iterations: dict[str, int] = {}
    step_live_values: dict[str, dict[Any, SettingValue]] = {}
    pending_generators: dict[str, Callable[[], None]] = {}
    step_by_id = {step.id: step for step in steps}
    forward_order = {step.id: index for index, step in enumerate(steps)}

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

    def failure_details(
        step: Step, error: Exception, location: dict[str, str | int | float | None],
    ) -> dict[str, Any]:
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
        return {
            "error_type": type(error).__name__,
            "traceback": "".join(trace.format()),
            **diagnostics,
        }

    def fail_loop_step(step: Step, error: Exception, step_iteration: int | None) -> None:
        unsuccessful.add(step.id)
        path, line = source_location(step.function)
        location: dict[str, str | int | float | None] = {"step_id": step.id, "file": path, "line": line}
        details = failure_details(step, error, location)
        if step_iteration is not None:
            details["iteration"] = step_iteration
        if on_event is not None:
            details["duration_ms"] = step_totals_ns.get(step.id, 0) / 1_000_000
        emit("failed", **location, **details)

    def execute_step(step: Step, *, in_loop: bool = False) -> None:
        step_iteration = None
        if in_loop:
            step_iteration = step_iterations.get(step.id, 0) + 1
            step_iterations[step.id] = step_iteration
        iteration_details = {"iteration": step_iteration} if step_iteration is not None else {}

        path, line = source_location(step.function)
        location: dict[str, str | int | float | None] = {"step_id": step.id, "file": path, "line": line}
        blocked_by = list(dict.fromkeys(
            loop_blockers.get(ref.step_id, ref.step_id)
            for ref in step.arguments.values()
            if isinstance(ref, Output) and ref.step_id in unsuccessful
        ))
        if blocked_by:
            unsuccessful.add(step.id)
            emit("skipped", **location, **iteration_details, blocked_by=blocked_by)
            return

        arguments = {
            name: (
                inputs[ref.name]
                if isinstance(ref, Input)
                else loop_values[(step.id, name)]
                if isinstance(ref, LoopOutput)
                else outputs[ref.step_id]
            )
            for name, ref in step.arguments.items()
        }
        argument_metadata = {}
        collect_samples = include_metadata and step.capture_samples is True
        if include_metadata:
            argument_metadata["input"] = {name: summarize(value) for name, value in arguments.items()}
        if collect_samples:
            argument_metadata["input_sample"] = capture_sample(arguments)
        values = step_live_values.setdefault(
            step.id,
            {
                setting: (settings or {}).get(step.id, {}).get(name, step.setting_defaults.get(name, setting.default))
                for name, setting in step.settings.items()
            },
        )
        controlled = live_settings if any(setting.live for setting in values) else None
        if controlled is not None:
            controlled.start_node(step.id)

        def apply_pending():
            for command in controlled.take(step.id):
                values[step.settings[command["name"]]] = command["value"]
                emit("running", step_id=step.id, **iteration_details, setting_change={**command, "status": "applied"})

        emit("running", **location, **iteration_details, **argument_metadata)
        reasons: list[str] | None = [] if include_metadata else None
        metrics: dict[str, dict[str, int | float]] | None = {} if include_metadata else None
        execution_ns = 0

        def invoke(function, /, *args, **kwargs):
            nonlocal execution_ns
            reason_scope = active_reasons.set(reasons)
            metrics_scope = active_metrics.set(metrics)
            output_scope = active_output.set(
                (lambda preview: emit("running", step_id=step.id, **iteration_details, output_preview=preview))
                if on_event is not None else None
            )
            frame_scope = active_frame.set(
                (lambda frame: emit("running", step_id=step.id, **iteration_details, live_frame=frame))
                if live_frames and on_event is not None else None
            )
            progress_scope = active_progress.set(
                (lambda stage, values: emit("running", step_id=step.id, **iteration_details, live_metrics={stage: values}))
                if live_metrics else None
            )
            settings_scope = active_settings.set(values or None)
            checkpoint_scope = active_checkpoint.set(apply_pending if controlled is not None else None)
            started = perf_counter_ns() if on_event is not None else None
            try:
                return function(*args, **kwargs)
            finally:
                if started is not None:
                    execution_ns += perf_counter_ns() - started
                active_reasons.reset(reason_scope)
                active_metrics.reset(metrics_scope)
                active_output.reset(output_scope)
                active_frame.reset(frame_scope)
                active_progress.reset(progress_scope)
                active_settings.reset(settings_scope)
                active_checkpoint.reset(checkpoint_scope)

        context = copy_context() if step.track_generator else None

        def finish(status: str, **details: Any) -> None:
            pending_generators.pop(step.id, None)
            if controlled is not None:
                for command in controlled.take(step.id, finish=True):
                    emit("running", step_id=step.id, **iteration_details, setting_change={
                        **command, "status": "rejected", "message": "The node ended before applying this change.",
                    })
            if reasons:
                details["reasons"] = reasons.copy()
            if metrics:
                details["metrics"] = metrics.copy()
            if on_event is not None:
                total_ns = step_totals_ns.get(step.id, 0) + execution_ns
                step_totals_ns[step.id] = total_ns
                details["duration_ms"] = total_ns / 1_000_000
                if step_iteration is not None:
                    details["iteration_duration_ms"] = execution_ns / 1_000_000
            emit(status, **location, **iteration_details, **details)

        def complete(value: Any) -> None:
            metadata: dict[str, Any] = {"output": summarize(value)} if include_metadata else {}
            if collect_samples:
                metadata["output_sample"] = capture_sample(value)
            finish("succeeded", **metadata)

        def fail(error: Exception) -> None:
            unsuccessful.add(step.id)
            finish("failed", **failure_details(step, error, location))

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
                finish("stopped")

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

    def execute_subgraph(
        node_ids: list[str],
        loop_edges: list[tuple[str, str, str, LoopOutput]],
        *,
        in_loop: bool = False,
    ) -> None:
        node_set = set(node_ids)
        adjacency: dict[str, list[str]] = {step_id: [] for step_id in node_ids}
        for step_id in node_ids:
            for ref in step_by_id[step_id].arguments.values():
                if isinstance(ref, Output) and ref.step_id in node_set:
                    adjacency[ref.step_id].append(step_id)
        for target_id, _, source_id, _ in loop_edges:
            if source_id in node_set and target_id in node_set:
                adjacency[source_id].append(target_id)

        for scc in _strongly_connected_components(node_ids, adjacency):
            scc_set = set(scc)
            scc_loops = [edge for edge in loop_edges if edge[0] in scc_set and edge[2] in scc_set]
            if not scc_loops:
                for step_id in sorted(scc, key=forward_order.__getitem__):
                    execute_step(step_by_id[step_id], in_loop=in_loop)
                continue

            ordered_scc = sorted(scc, key=forward_order.__getitem__)
            head_id = next(step_id for step_id in ordered_scc if any(edge[0] == step_id for edge in scc_loops))
            head_edges = [edge for edge in scc_loops if edge[0] == head_id]
            inner_edges = [edge for edge in scc_loops if edge[0] != head_id]
            for target_id, arg_name, _, ref in head_edges:
                loop_values[(target_id, arg_name)] = ref.initial
            has_until = any(ref.until is not None for _, _, _, ref in head_edges)
            limit = min(
                (ref.max_iterations if ref.max_iterations is not None else DEFAULT_MAX_LOOP_ITERATIONS)
                for _, _, _, ref in head_edges
            )

            iteration = 0
            while True:
                iteration += 1
                try:
                    execute_subgraph(ordered_scc, inner_edges, in_loop=True)
                finally:
                    for step_id in ordered_scc:
                        close = pending_generators.get(step_id)
                        if close is not None:
                            close()

                if any(step_id in unsuccessful for step_id in ordered_scc):
                    failed_id = next(step_id for step_id in ordered_scc if step_id in unsuccessful)
                    blocker = loop_blockers.get(failed_id, failed_id)
                    for step_id in ordered_scc:
                        unsuccessful.add(step_id)
                        loop_blockers.setdefault(step_id, blocker)
                    break

                for target_id, arg_name, source_id, _ in head_edges:
                    loop_values[(target_id, arg_name)] = outputs[source_id]

                if has_until:
                    try:
                        done = all(
                            ref.until(outputs[source_id])
                            for _, _, source_id, ref in head_edges
                            if ref.until is not None
                        )
                    except Exception as error:
                        fail_loop_step(step_by_id[head_id], error, step_iterations.get(head_id))
                        for step_id in ordered_scc:
                            unsuccessful.add(step_id)
                            loop_blockers[step_id] = head_id
                        break
                    if done:
                        break
                    if iteration >= limit:
                        fail_loop_step(
                            step_by_id[head_id],
                            LoopLimitError(
                                f"Loop at {head_id!r} did not satisfy its stop condition within {limit} iterations."
                            ),
                            step_iterations.get(head_id),
                        )
                        for step_id in ordered_scc:
                            unsuccessful.add(step_id)
                            loop_blockers[step_id] = head_id
                        break
                elif iteration >= limit:
                    break

    all_loop_edges = [
        (step.id, name, ref.step_id, ref)
        for step in steps
        for name, ref in step.arguments.items()
        if isinstance(ref, LoopOutput)
    ]

    try:
        try:
            execute_subgraph([step.id for step in steps], all_loop_edges)
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
