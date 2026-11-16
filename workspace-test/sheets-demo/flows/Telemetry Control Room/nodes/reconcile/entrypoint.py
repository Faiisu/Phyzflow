from collections import Counter

from nodes.create_batch.entrypoint import Batch


def reconcile(batch: Batch, processed: dict) -> dict:
    """Require an exclusive terminal outcome for every unique event."""
    expected = {event.id for event in batch.events}
    scored = set(processed["scores"])
    dead_letters = set(processed["dead_letters"])
    if scored & dead_letters or scored | dead_letters != expected:
        raise ValueError("Some events have missing or conflicting outcomes.")
    return {
        "source_records": batch.source_records,
        "duplicates_removed": batch.source_records - len(expected),
        "unique_events": len(expected),
        "scored": len(scored),
        "dead_letters": len(dead_letters),
        "dead_letters_by_reason": dict(sorted(Counter(processed["dead_letters"].values()).items())),
    }
