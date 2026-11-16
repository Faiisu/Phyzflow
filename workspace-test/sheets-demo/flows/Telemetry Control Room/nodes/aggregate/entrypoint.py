from collections import Counter


def aggregate(processed: dict) -> dict:
    """Group scores by severity and identify critical events for delivery."""
    scores = processed["scores"]
    devices = {}
    for result in scores.values():
        devices.setdefault(result.device, Counter())[result.band] += 1
    return {
        "by_severity": dict(sorted(Counter(result.band for result in scores.values()).items())),
        "by_device": {str(device): dict(sorted(counts.items())) for device, counts in sorted(devices.items())},
        "critical_ids": sorted(key for key, result in scores.items() if result.band == "critical"),
    }
