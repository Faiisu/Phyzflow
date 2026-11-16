def report(processed: dict, audit: dict, aggregates: dict, receipt: dict, delivery: dict) -> dict:
    """Return a compact control-room report for the UI and CLI."""
    return {
        "events": audit,
        "alerts": {**delivery, **aggregates},
        "storage": receipt,
        "pipeline": processed["metrics"],
    }
