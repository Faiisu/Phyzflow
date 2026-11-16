from dataclasses import dataclass
from random import Random


@dataclass(frozen=True)
class Event:
    id: int
    device: int
    samples: tuple[int, ...]


@dataclass(frozen=True)
class Batch:
    events: tuple[Event, ...]
    source_records: int
    seed: int
    io_workers: int
    cpu_workers: int
    queue_capacity: int
    score_rounds: int
    failure: str


def create_batch(config: dict) -> Batch:
    """Generate deterministic device readings and remove replayed records."""
    if not isinstance(config, dict):
        raise ValueError("Config must be an object.")
    limits = {
        "events": (240, 1, 2000),
        "seed": (42, 0, 2**32 - 1),
        "io_workers": (4, 1, 8),
        "cpu_workers": (3, 1, 8),
        "queue_capacity": (6, 1, 128),
        "score_rounds": (320, 1, 1000),
    }
    if config.keys() - (limits.keys() | {"simulate"}):
        raise ValueError("Config contains unknown fields.")
    values = {}
    for name, (default, minimum, maximum) in limits.items():
        value = config.get(name, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
        values[name] = value
    failure = config.get("simulate", "none")
    if failure not in ("none", "score", "delivery"):
        raise ValueError("simulate must be none, score, or delivery.")

    random = Random(values["seed"])
    source = []
    for event_id in range(1, values.pop("events") + 1):
        level = 80 if event_id % 17 == 0 else 55 if event_id % 7 == 0 else 30
        event = Event(event_id, event_id % 12, tuple(level + random.randint(-5, 5) for _ in range(64)))
        source.append(event)
        if event_id % 11 == 0:
            source.append(event)
    unique = {event.id: event for event in source}
    return Batch(tuple(unique.values()), len(source), failure=failure, **values)
