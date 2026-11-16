from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from nodes.create_batch.entrypoint import Batch


DATABASE = Path(__file__).resolve().parents[2] / ".workspace" / "telemetry.sqlite3"
SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_key TEXT PRIMARY KEY,
    audit TEXT NOT NULL,
    aggregates TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (
    batch_key TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    distance INTEGER,
    PRIMARY KEY (batch_key, event_id),
    FOREIGN KEY (batch_key) REFERENCES batches(batch_key)
);
CREATE TABLE IF NOT EXISTS outbox (
    batch_key TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (batch_key, event_id),
    FOREIGN KEY (batch_key, event_id) REFERENCES outcomes(batch_key, event_id)
);
"""


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def persist(batch: Batch, audit: dict, aggregates: dict, processed: dict) -> dict:
    """Save the batch, outcomes, and unsent alerts in the same transaction."""
    scores = processed["scores"]
    dead_letters = processed["dead_letters"]
    outcomes = [
        (event.id, scores[event.id].band, scores[event.id].window_distance)
        if event.id in scores else (event.id, dead_letters[event.id], None)
        for event in batch.events
    ]
    content = (batch.seed, batch.score_rounds, audit, aggregates, outcomes)
    batch_key = sha256(encode(content).encode()).hexdigest()
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DATABASE)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        with connection:
            inserted = connection.execute(
                "INSERT INTO batches VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (batch_key, encode(audit), encode(aggregates)),
            ).rowcount
            if inserted:
                connection.executemany(
                    "INSERT INTO outcomes VALUES (?, ?, ?, ?)",
                    ((batch_key, *outcome) for outcome in outcomes),
                )
                connection.executemany(
                    "INSERT INTO outbox (batch_key, event_id) VALUES (?, ?)",
                    ((batch_key, event_id) for event_id in aggregates["critical_ids"]),
                )
    return {"batch_key": batch_key, "replayed": not bool(inserted), "database": str(DATABASE)}
