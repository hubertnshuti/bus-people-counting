import sqlite3
import datetime
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "bus.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id        TEXT    NOT NULL,
            event_type       TEXT    NOT NULL,
            count_after      INTEGER NOT NULL,
            device_uptime_ms INTEGER,
            server_time      TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS device_state (
            device_id   TEXT    PRIMARY KEY,
            capacity    INTEGER NOT NULL DEFAULT 10,
            paused      INTEGER NOT NULL DEFAULT 0,
            reset_token INTEGER NOT NULL DEFAULT 0,
            updated_at  TEXT
        )
    """)
    conn.execute("""
        INSERT OR IGNORE INTO device_state (device_id, capacity, paused, reset_token, updated_at)
        VALUES ('bus01', 10, 0, 0, ?)
    """, (datetime.datetime.now().isoformat(),))
    conn.commit()
    conn.close()
    print(f"[db] ready at {DB_PATH}")
