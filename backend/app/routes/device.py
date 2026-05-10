import datetime
from fastapi import APIRouter
from ..database import get_db
from ..models import StateUpdate, DeviceStateResponse

router = APIRouter(prefix="/device", tags=["device"])


@router.get("/state", response_model=DeviceStateResponse)
def get_device_state(device_id: str = "bus01"):
    """ESP32 polls this every second. Keeps the payload tiny and fast."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM device_state WHERE device_id = ?", (device_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"capacity": 10, "paused": False, "reset_token": 0}
    return {
        "capacity":    row["capacity"],
        "paused":      bool(row["paused"]),
        "reset_token": row["reset_token"],
    }


@router.post("/state")
def update_device_state(update: StateUpdate, device_id: str = "bus01"):
    """Dashboard sends control commands here. do_reset bumps reset_token so
    the ESP32 zeros its count once — prevents accidental repeated resets."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM device_state WHERE device_id = ?", (device_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"error": "device not found"}

    capacity    = update.capacity if update.capacity is not None else row["capacity"]
    paused      = (1 if update.paused else 0) if update.paused is not None else row["paused"]
    reset_token = row["reset_token"] + (1 if update.do_reset else 0)

    conn.execute(
        """UPDATE device_state
           SET capacity=?, paused=?, reset_token=?, updated_at=?
           WHERE device_id=?""",
        (capacity, paused, reset_token, datetime.datetime.now().isoformat(), device_id)
    )
    conn.commit()
    conn.close()
    print(f"[control] capacity={capacity} paused={bool(paused)} reset_token={reset_token}")
    return {
        "capacity":    capacity,
        "paused":      bool(paused),
        "reset_token": reset_token,
    }
