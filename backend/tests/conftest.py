"""
conftest.py — pytest's shared setup file.

Two things happen here before any test runs:

1.  We point BUS_DB_PATH at a temp file so tests never touch the real bus.db.
    This line must run BEFORE we import anything from the app, because
    database.py reads the env var at import time.

2.  We define fixtures — reusable pieces of setup/teardown that any test
    can ask for just by naming them as function parameters.
"""
import os
import tempfile
import pytest

# Step 1: redirect the database BEFORE the app is imported
_fd, _tmp_db = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["BUS_DB_PATH"] = _tmp_db

# Step 2: now it is safe to import the app
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app                   # noqa: E402
from app.database import get_db            # noqa: E402


@pytest.fixture
def client():
    """
    Gives each test a fresh TestClient.
    Using it as a context manager (with TestClient(...) as c) triggers
    the FastAPI lifespan, which calls init_db() — so the schema is always
    ready before the test body runs.
    """
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_db(client):
    """
    autouse=True means this runs automatically for every test, no need
    to ask for it explicitly.  The 'yield' splits setup (before) from
    teardown (after).  Here we only need teardown: wipe data between tests
    so they never interfere with each other.
    """
    yield  # test runs here
    conn = get_db()
    conn.execute("DELETE FROM events")
    conn.execute(
        "UPDATE device_state "
        "SET capacity=10, paused=0, reset_token=0 "
        "WHERE device_id='bus01'"
    )
    conn.commit()
    conn.close()
