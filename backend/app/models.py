from pydantic import BaseModel
from typing import Optional


class IncomingEvent(BaseModel):
    device: str
    count: int
    uptime_ms: int
    event_type: str = "snapshot"


class StateUpdate(BaseModel):
    capacity: Optional[int] = None
    paused: Optional[bool] = None
    do_reset: bool = False


class EventResponse(BaseModel):
    status: str
    server_time: str


class DeviceStateResponse(BaseModel):
    capacity: int
    paused: bool
    reset_token: int
