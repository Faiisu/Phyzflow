from workspace_sdk import record_metrics


def summarize(readings: list[int]) -> dict[str, int | float]:
    """Summarize the collected batch."""
    mean = round(sum(readings) / len(readings), 2)
    record_metrics("Readings", {"Mean": mean})
    return {"count": len(readings), "mean": mean}
