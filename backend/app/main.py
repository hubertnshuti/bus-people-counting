import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .database import init_db
from .routes import events, device


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once when the server starts (or when TestClient opens).
    # Putting init_db() here instead of at module level means tests
    # can set BUS_DB_PATH before the DB file is created.
    init_db()
    yield
    # anything after yield runs on shutdown — nothing to clean up here


app = FastAPI(
    title="Bus Counter API",
    description="Receives events from ESP32 and provides device control endpoints.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(events.router)
app.include_router(device.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "time": datetime.datetime.now().isoformat()}
