from collections.abc import Iterable
from time import perf_counter

from workspace_sdk import publish_metrics, publish_output


def multiply_by_ten(values: Iterable[int]) -> dict[str, int] | None:
    """Publish the latest input and its product with ten."""
    started = perf_counter()
    latest = None
    for value in values:
        latest = {"count": value, "output": value * 10}
        publish_output(latest)
        publish_metrics("Counter", {
            "Elapsed seconds": perf_counter() - started,
            "Count": value,
            "Output": latest["output"],
        })
    return latest
