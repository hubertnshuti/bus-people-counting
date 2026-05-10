from fastapi import FastAPI
from pydantic import BaseModel
import sqlite3
import datetime
from .database import get_db, init_db

app = FastAPI(title="Bus Counter API")

init_db()


class IncomingEvent(BaseModel):
    device: str
    count: int
    uptime_ms: int
    event_type: str = "snapshot"


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.datetime.now().isoformat()}


@app.post("/events")
def receive_event(event: IncomingEvent):
    server_time = datetime.datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        """INSERT INTO events
           (device_id, event_type, count_after, device_uptime_ms, server_time)
           VALUES (?, ?, ?, ?, ?)""",
        (event.device, event.event_type, event.count, event.uptime_ms, server_time)
    )
    conn.commit()
    conn.close()
    print(f"[event] {server_time}  {event.device}  {event.event_type}  count={event.count}")
    return {"status": "saved", "server_time": server_time}


@app.get("/events/recent")
def recent_events(limit: int = 20):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return {"count": len(rows), "events": [dict(r) for r in rows]}
