from dataclasses import dataclass
from multiprocessing import parent_process
from os import _exit, getpid
from threading import Thread
from time import perf_counter_ns


@dataclass(frozen=True)
class Score:
    event_id: int
    device: int
    band: str
    window_distance: int
    pid: int
    started_ns: int
    finished_ns: int


def watch_sheet_process() -> None:
    parent = parent_process()
    if parent is None:
        return

    def exit_with_parent():
        parent.join()
        # Workspace Stop can terminate the sheet before its executor shuts down.
        _exit(1)

    Thread(target=exit_with_parent, name="watch-sheet", daemon=True).start()


def score(event_id: int, device: int, samples: tuple[int, ...], rounds: int, fail: bool) -> Score:
    """Classify a calibrated window by its distance from three reference profiles."""
    started = perf_counter_ns()
    if fail and event_id == 1:
        raise RuntimeError("Simulated CPU scoring failure.")
    distances = {}
    for band, level in (("normal", 30), ("warning", 55), ("critical", 80)):
        distance = 0
        for offset in range(rounds):
            distance += sum(
                (sample - level - ((index + offset) % 5 - 2)) ** 2
                for index, sample in enumerate(samples)
            )
        distances[band] = distance
    band = min(distances, key=distances.__getitem__)
    return Score(event_id, device, band, distances[band], getpid(), started, perf_counter_ns())
