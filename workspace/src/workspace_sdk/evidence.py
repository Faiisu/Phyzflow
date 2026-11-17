from base64 import b64encode
from contextvars import ContextVar
from itertools import islice
import json
from math import isfinite
from typing import Any, Callable


active_reasons: ContextVar[list[str] | None] = ContextVar("active_reasons", default=None)
active_metrics: ContextVar[dict[str, dict[str, int | float]] | None] = ContextVar("active_metrics", default=None)
active_output: ContextVar[Callable[[str], None] | None] = ContextVar("active_output", default=None)
active_frame: ContextVar[Callable[[str], None] | None] = ContextVar("active_frame", default=None)
active_progress: ContextVar[Callable[[str, dict[str, int | float]], None] | None] = ContextVar(
    "active_progress", default=None
)


def record_reason(reason: str) -> None:
    """Record a decision reason for the executing node when collecting history."""
    reasons = active_reasons.get()
    if reasons is not None and type(reason) is str and len(reasons) < 10:
        reasons.append(reason[:512])


def record_metrics(stage: str, values: dict[str, int | float]) -> None:
    """Report numeric aggregates with fixed labels.

    A node can report up to eight groups. Each call checks the first eight
    entries and keeps only supported numeric values.
    """
    groups = active_metrics.get()
    if groups is None or type(stage) is not str or (stage not in groups and len(groups) >= 8):
        return
    if metrics := checked_metrics(stage, values):
        groups[stage] = metrics


def checked_metrics(stage: str, values: dict[str, int | float]) -> dict[str, int | float]:
    if type(stage) is not str or not 1 <= len(stage) <= 48 or type(values) is not dict:
        return {}
    return {
        name: value
        for name, value in islice(values.items(), 8)
        if type(name) is str and 1 <= len(name) <= 48
        and ((type(value) is int and value.bit_length() <= 53) or (type(value) is float and isfinite(value)))
    }


def publish_metrics(stage: str, values: dict[str, int | float]) -> None:
    """Publish a numeric progress snapshot to the live UI without saving it."""
    observer = active_progress.get()
    if observer is not None and (metrics := checked_metrics(stage, values)):
        observer(stage, metrics)


def publish_output(value: Any) -> None:
    """Send a bounded JSON preview to the current observer without saving it to history."""
    observer = active_output.get()
    if observer is not None and (preview := capture_sample(value)) is not None:
        observer(preview)


def publish_frame(jpeg: bytes) -> None:
    """Send a JPEG of up to 256 KiB to the live dashboard."""
    observer = active_frame.get()
    if observer is None:
        return
    if type(jpeg) is not bytes or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
        raise ValueError("A live frame must be JPEG bytes.")
    if len(jpeg) > 256 * 1024:
        raise ValueError("A live frame must be at most 256 KiB.")
    observer(b64encode(jpeg).decode("ascii"))


def capture_sample(value: Any) -> str | None:
    """Capture a bounded JSON sample without converting custom objects to text."""
    limit = 4096
    remaining = limit

    def snapshot(item: Any) -> Any:
        nonlocal remaining
        kind = type(item)
        remaining -= 1 + (len(item) if kind is str else 0)
        if remaining < 0 or (kind is int and item.bit_length() > limit * 4):
            raise ValueError("Sample exceeds its size limit.")
        if item is None or kind is str or kind is bool or kind is int or kind is float:
            return item
        if kind is list or kind is tuple:
            return [snapshot(child) for child in item]
        if kind is dict:
            result = {}
            for key, child in item.items():
                if type(key) is not str:
                    raise TypeError("Sample keys must be strings.")
                result[snapshot(key)] = snapshot(child)
            return result
        raise TypeError("The value has no JSON sample.")

    try:
        encoded = json.dumps(snapshot(value), allow_nan=False, indent=2)
    except (TypeError, ValueError, RuntimeError):
        return None
    return encoded if len(encoded) <= limit else None
