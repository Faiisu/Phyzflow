from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from dataclasses import dataclass
from math import ceil
from multiprocessing import get_context
from queue import Empty, Full, Queue
from threading import Event, current_thread
from time import perf_counter_ns, sleep

from workspace_sdk import record_metrics

from nodes.create_batch.entrypoint import Batch, Event as Reading

from .scoring import score, watch_sheet_process


@dataclass(frozen=True)
class Prepared:
    event: Reading
    samples: tuple[int, ...]
    attempts: int
    reason: str | None
    queued_ns: int
    started_ns: int
    finished_ns: int
    thread: str


class BoundedQueue(Queue):
    def _init(self, maxsize):
        super()._init(maxsize)
        self.peak = 0
        self.blocked_puts = 0

    def _put(self, item):
        super()._put(item)
        self.peak = max(self.peak, self._qsize())

    def offer(self, item, stop: Event):
        try:
            self.put_nowait(item)
            return
        except Full:
            with self.mutex:
                self.blocked_puts += 1
        while not stop.is_set():
            try:
                self.put(item, timeout=0.05)
                return
            except Full:
                continue

    def metrics(self):
        return {"capacity": self.maxsize, "peak_depth": self.peak, "blocked_puts": self.blocked_puts}


def prepare(event: Reading, queued_ns: int) -> Prepared:
    started = perf_counter_ns()
    attempts = 0
    reason = None
    samples = ()
    if event.id % 37 == 0:
        reason = "invalid_checksum"
    else:
        for attempts in range(1, 4):
            sleep(0.002)  # Simulated latency for a calibration lookup.
            if event.id % 53 != 0 and (event.id % 13 != 0 or attempts > 1):
                bias = event.device % 3 - 1
                samples = tuple(sample - bias for sample in event.samples)
                break
            if attempts == 3:
                reason = "lookup_timeout"
            else:
                sleep(attempts * 0.002)
    return Prepared(event, samples, attempts, reason, queued_ns, started, perf_counter_ns(), current_thread().name)


def p95_ms(values):
    ordered = sorted(values)
    return round(ordered[ceil(len(ordered) * 0.95) - 1] / 1_000_000, 3) if ordered else 0.0


def peak_overlap(spans):
    edges = [(time, delta) for start, end in spans for time, delta in ((start, 1), (end, -1))]
    active = peak = 0
    for _, delta in sorted(edges):
        active += delta
        peak = max(peak, active)
    return peak


def stream_pipeline(batch: Batch) -> dict:
    """Overlap queue intake, calibration threads, and CPU scoring processes."""
    started = perf_counter_ns()
    stop = Event()
    ingress = BoundedQueue(batch.queue_capacity)
    ready = BoundedQueue(batch.queue_capacity)
    prepared = {}
    scored = {}
    pending = set()
    peak_inflight = 0

    def produce():
        for event in batch.events:
            if stop.is_set():
                return
            ingress.offer((event, perf_counter_ns()), stop)
        for _ in range(batch.io_workers):
            ingress.offer(None, stop)

    def enrich():
        while not stop.is_set():
            try:
                task = ingress.get(timeout=0.05)
            except Empty:
                continue
            if task is None:
                return
            ready.offer(prepare(*task), stop)

    with (
        ProcessPoolExecutor(
            batch.cpu_workers,
            mp_context=get_context("spawn"),
            initializer=watch_sheet_process,
        ) as processes,
        ThreadPoolExecutor(batch.io_workers + 1, thread_name_prefix="telemetry-io") as threads,
    ):
        workers = [threads.submit(produce)]
        workers.extend(threads.submit(enrich) for _ in range(batch.io_workers))
        try:
            while len(prepared) < len(batch.events) or pending:
                for worker in workers:
                    if worker.done():
                        worker.result()
                if pending and (len(pending) >= batch.cpu_workers * 2 or
                                len(prepared) == len(batch.events)):
                    completed, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in completed:
                        result = future.result()
                        scored[result.event_id] = result
                    continue
                try:
                    item = ready.get(timeout=0.05)
                except Empty:
                    continue
                prepared[item.event.id] = item
                if item.reason is None:
                    pending.add(processes.submit(
                        score, item.event.id, item.event.device, item.samples, batch.score_rounds,
                        batch.failure == "score",
                    ))
                    peak_inflight = max(peak_inflight, len(pending))
        finally:
            stop.set()
            for future in pending:
                future.cancel()

    elapsed = (perf_counter_ns() - started) / 1_000_000_000
    io_counts = Counter(item.thread for item in prepared.values())
    cpu_counts = Counter(item.pid for item in scored.values())
    dead_letters = {event_id: item.reason for event_id, item in prepared.items() if item.reason}
    ingress_metrics = ingress.metrics()
    scoring_metrics = ready.metrics()
    io_overlap = peak_overlap((item.started_ns, item.finished_ns) for item in prepared.values())
    cpu_overlap = peak_overlap((item.started_ns, item.finished_ns) for item in scored.values())
    ingress_wait = p95_ms(item.started_ns - item.queued_ns for item in prepared.values())
    scoring_wait = p95_ms(item.started_ns - prepared[key].finished_ns for key, item in scored.items())
    record_queue_metrics("Ingress queue", ingress_metrics, ingress_wait)
    record_metrics("I/O workers", {"Peak overlap": io_overlap})
    record_queue_metrics("Scoring queue", scoring_metrics, scoring_wait)
    record_metrics("CPU workers", {"Peak overlap": cpu_overlap})
    return {
        "scores": scored,
        "dead_letters": dead_letters,
        "metrics": {
            "elapsed_ms": round(elapsed * 1000, 2),
            "records_per_second": round(len(prepared) / elapsed, 1),
            "queues": {"ingress": ingress_metrics, "ready_to_score": scoring_metrics},
            "io_records_by_thread": dict(sorted(io_counts.items())),
            "scored_by_process": {str(pid): count for pid, count in sorted(cpu_counts.items())},
            "peak_io_overlap": io_overlap,
            "peak_cpu_overlap": cpu_overlap,
            "peak_cpu_inflight": peak_inflight,
            "extra_attempts": sum(max(0, item.attempts - 1) for item in prepared.values()),
            "recovered_after_retry": sum(item.attempts > 1 and item.reason is None for item in prepared.values()),
            "p95_ingress_wait_ms": ingress_wait,
            "p95_io_ms": p95_ms(item.finished_ns - item.started_ns for item in prepared.values()),
            "p95_ready_wait_ms": scoring_wait,
            "p95_cpu_ms": p95_ms(item.finished_ns - item.started_ns for item in scored.values()),
            "p95_scored_event_ms": p95_ms(item.finished_ns - prepared[key].queued_ns for key, item in scored.items()),
        },
    }


def record_queue_metrics(stage: str, metrics: dict[str, int], wait_ms: float) -> None:
    record_metrics(stage, {
        "Capacity": metrics["capacity"],
        "Peak depth": metrics["peak_depth"],
        "Blocked enqueues": metrics["blocked_puts"],
        "p95 wait (ms)": wait_ms,
    })
