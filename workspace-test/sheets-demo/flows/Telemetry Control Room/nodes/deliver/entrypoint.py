from contextlib import closing
import sqlite3


def deliver(receipt: dict, config: dict) -> dict:
    """Deliver pending alerts to a local sink and mark them atomically."""
    key = receipt["batch_key"]
    with closing(sqlite3.connect(receipt["database"])) as connection, connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS local_alerts (
                batch_key TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                PRIMARY KEY (batch_key, event_id)
            )
        """)
        new = connection.execute("""
            INSERT INTO local_alerts (batch_key, event_id)
            SELECT batch_key, event_id FROM outbox
            WHERE batch_key = ? AND delivered = 0
        """, (key,)).rowcount
        if config.get("simulate") == "delivery":
            raise RuntimeError("Simulated delivery interruption before commit.")
        connection.execute("UPDATE outbox SET delivered = 1 WHERE batch_key = ? AND delivered = 0", (key,))
        total = connection.execute("SELECT COUNT(*) FROM local_alerts WHERE batch_key = ?", (key,)).fetchone()[0]
    return {"newly_delivered": new, "total_delivered": total}
