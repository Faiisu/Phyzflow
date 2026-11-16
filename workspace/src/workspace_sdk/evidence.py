from contextvars import ContextVar
import json
from typing import Any


active_reasons: ContextVar[list[str] | None] = ContextVar("active_reasons", default=None)


def record_reason(reason: str) -> None:
    """Record a decision reason for the executing node when collecting history."""
    reasons = active_reasons.get()
    if reasons is not None and type(reason) is str and len(reasons) < 10:
        reasons.append(reason[:512])


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
