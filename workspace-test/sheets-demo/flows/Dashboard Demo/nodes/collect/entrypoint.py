from time import sleep

from workspace_sdk import publish_metrics, publish_output, record_metrics


def collect(count: int) -> list[int]:
    """Simulate a sensor producing a small batch of readings."""
    if type(count) is not int or not 1 <= count <= 40:
        raise ValueError("Count must be an integer from 1 to 40.")
    readings = []
    for index in range(count):
        sleep(0.18)
        readings.append(20 + index % 8)
        publish_output({"collected": len(readings), "target": count})
        publish_metrics("Readings", {"Collected": len(readings), "Target": count})
    record_metrics("Readings", {"Collected": len(readings), "Target": count})
    return readings
