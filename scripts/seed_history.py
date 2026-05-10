"""
Seed 14 days of realistic historical boarding data into bus.db.

Patterns:
  - Morning rush 6-9 AM: heavy boarding
  - Lunch 12-1 PM: moderate
  - Afternoon rush 4-7 PM: heavy boarding + alighting
  - Late evening 8-10 PM: light
  - Overnight 11 PM - 5 AM: almost nothing
  - Weekends: ~50% lighter, no morning rush

Safe to re-run — deletes previous seeded rows before inserting.
Real events (device_id='bus01') are never touched.

Usage:
    python scripts/seed_history.py
"""

import sqlite3
import random
import datetime
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "bus.db")


def expected_events_for(weekday, hour):
    """weekday: 0=Mon..6=Sun. hour: 0..23."""
    is_weekend = weekday >= 5

    if hour < 5 or hour >= 23:
        base = 0
    elif 6 <= hour <= 8:
        base = 28 if not is_weekend else 8
    elif 9 <= hour <= 11:
        base = 12 if not is_weekend else 9
    elif hour == 12:
        base = 16 if not is_weekend else 11
    elif 13 <= hour <= 15:
        base = 10 if not is_weekend else 9
    elif 16 <= hour <= 18:
        base = 30 if not is_weekend else 12
    elif 19 <= hour <= 20:
        base = 14 if not is_weekend else 10
    else:
        base = 6 if not is_weekend else 5

    jitter = random.uniform(0.7, 1.3)
    return max(0, int(base * jitter))


def seed():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: {DB_PATH} doesn't exist.")
        print("Run the FastAPI server at least once first so the tables are created.")
        return

    conn = sqlite3.connect(DB_PATH)

    deleted = conn.execute(
        "DELETE FROM events WHERE device_id = ?", ("bus01-seed",)
    ).rowcount
    if deleted:
        print(f"[seed] removed {deleted} previous seeded rows")

    now           = datetime.datetime.now()
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    BUS_CAPACITY   = 10
    running_count  = 0
    inserted       = 0

    for days_ago in range(14, 0, -1):
        day     = today_midnight - datetime.timedelta(days=days_ago)
        weekday = day.weekday()

        for hour in range(24):
            n = expected_events_for(weekday, hour)
            for _ in range(n):
                ts = day + datetime.timedelta(
                    hours=hour,
                    minutes=random.randint(0, 59),
                    seconds=random.randint(0, 59),
                    microseconds=random.randint(0, 999999),
                )
                if hour < 10:
                    p_entry = 0.75
                elif hour < 16:
                    p_entry = 0.55
                else:
                    p_entry = 0.45

                if running_count <= 0:
                    is_entry = True
                elif running_count >= BUS_CAPACITY:
                    is_entry = False
                else:
                    is_entry = random.random() < p_entry

                running_count += 1 if is_entry else -1

                conn.execute(
                    """INSERT INTO events
                       (device_id, event_type, count_after, device_uptime_ms, server_time)
                       VALUES (?, ?, ?, ?, ?)""",
                    ("bus01-seed",
                     "entry" if is_entry else "exit",
                     running_count,
                     0,
                     ts.isoformat())
                )
                inserted += 1

    conn.commit()
    conn.close()
    print(f"[seed] inserted {inserted} events over 14 days")
    print("[seed] device_id='bus01-seed' — real events are untouched")
    print("Open the dashboard, heatmap should show clear rush hour patterns now.")


if __name__ == "__main__":
    seed()
