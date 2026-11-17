from collections.abc import Iterable

from workspace_sdk import publish_output


def multiply_by_ten(values: Iterable[int]) -> dict[str, int] | None:
    """Publish the latest input and its product with ten."""
    latest = None
    for value in values:
        latest = {"count": value, "output": value * 10}
        publish_output(latest)
    return latest
