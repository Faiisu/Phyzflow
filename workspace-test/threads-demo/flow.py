from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier, current_thread
from time import sleep

from workspace_sdk import Flow, Input, Output, Step


WORKERS = {"A": 0, "B": 10, "C": 20}


@dataclass(frozen=True)
class StageOutput:
    thread: str
    value: int


class Workers:
    def __init__(self, value: int):
        self.value = value
        self.start = Barrier(len(WORKERS), timeout=5)
        self.executors = {
            name: ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"worker-{name}")
            for name in WORKERS
        }

    def close(self) -> None:
        for executor in self.executors.values():
            executor.shutdown()


@dataclass(frozen=True)
class ScheduledStage:
    workers: Workers
    future: Future[StageOutput]


def fork(value: int) -> Workers:
    """Set up one single-thread executor for each worker."""
    if type(value) is not int:
        raise ValueError("Value must be an integer.")
    return Workers(value)


def schedule(worker: str, transform: Callable[[int], int]):
    def submit_stage(previous: Workers | ScheduledStage) -> ScheduledStage:
        workers = previous if isinstance(previous, Workers) else previous.workers

        def run_stage() -> StageOutput:
            if isinstance(previous, Workers):
                workers.start.wait()
                incoming = workers.value + WORKERS[worker]
            else:
                incoming = previous.future.result().value
            sleep(0.08)
            return StageOutput(current_thread().name, transform(incoming))

        try:
            future = workers.executors[worker].submit(run_stage)
        except Exception:
            workers.start.abort()
            workers.close()
            raise
        return ScheduledStage(workers, future)

    return submit_stage


def collect(**stages: ScheduledStage) -> dict:
    """Wait for all twelve stage outputs and return their worker names and values."""
    workers = next(iter(stages.values())).workers
    try:
        outputs = {name: stage.future.result() for name, stage in stages.items()}
        return {
            "thread_count": len({output.thread for output in outputs.values()}),
            "outputs": {
                name: {"thread": output.thread, "value": output.value}
                for name, output in outputs.items()
            },
        }
    finally:
        workers.close()


def build_flow() -> Flow:
    steps = [Step("Fork", fork, {"value": Input("value")})]
    outputs = {}
    for worker in WORKERS:
        previous = "Fork"
        for name, transform in (
            ("Prepare", lambda value: value),
            ("Double", lambda value: value * 2),
            ("Add One", lambda value: value + 1),
            ("Square", lambda value: value * value),
        ):
            stage = f"{worker} {name}"
            steps.append(Step(stage, schedule(worker, transform), {"previous": Output(previous)}))
            outputs[stage] = Output(stage)
            previous = stage
    steps.append(Step("Collect", collect, outputs))
    return Flow(inputs=("value",), steps=tuple(steps), output=Output("Collect"))


flow = build_flow()
