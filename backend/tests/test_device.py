"""
Tests for GET /device/state and POST /device/state.

These test the control plane — the mechanism the dashboard uses
to send capacity changes, pause, and reset to the ESP32.
"""


def test_get_default_state(client):
    response = client.get("/device/state")
    assert response.status_code == 200
    data = response.json()
    assert data["capacity"] == 10
    assert data["paused"] is False
    assert data["reset_token"] == 0


def test_update_capacity(client):
    response = client.post("/device/state", json={"capacity": 30})
    assert response.status_code == 200
    assert response.json()["capacity"] == 30


def test_capacity_is_persisted(client):
    client.post("/device/state", json={"capacity": 25})
    # fetch again — should still be 25
    response = client.get("/device/state")
    assert response.json()["capacity"] == 25


def test_pause_device(client):
    response = client.post("/device/state", json={"paused": True})
    assert response.status_code == 200
    assert response.json()["paused"] is True


def test_unpause_device(client):
    client.post("/device/state", json={"paused": True})
    response = client.post("/device/state", json={"paused": False})
    assert response.json()["paused"] is False


def test_reset_increments_token(client):
    before = client.get("/device/state").json()["reset_token"]
    client.post("/device/state", json={"do_reset": True})
    after = client.get("/device/state").json()["reset_token"]
    assert after == before + 1


def test_reset_twice_increments_twice(client):
    client.post("/device/state", json={"do_reset": True})
    client.post("/device/state", json={"do_reset": True})
    token = client.get("/device/state").json()["reset_token"]
    assert token == 2


def test_partial_update_does_not_clobber_other_fields(client):
    # Set capacity, then update only paused — capacity must survive
    client.post("/device/state", json={"capacity": 40})
    client.post("/device/state", json={"paused": True})
    state = client.get("/device/state").json()
    assert state["capacity"] == 40
    assert state["paused"] is True
