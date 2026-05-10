import datetime
from fastapi import FastAPI
from .database import init_db
from .routes import events, device

app = FastAPI(
    title="Bus Counter API",
    description="Receives events from ESP32 and provides device control endpoints.",
    version="1.0.0",
)

init_db()

app.include_router(events.router)
app.include_router(device.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "time": datetime.datetime.now().isoformat()}
