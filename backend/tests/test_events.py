"""
Tests for POST /events and GET /events/recent.

Each function is one test.  pytest finds them automatically because
the names start with 'test_'.  The 'client' parameter is injected
by conftest.py — pytest sees the name, finds the matching fixture, runs it.
"""


def test_post_event_success(client):
    response = client.post("/events", json={
        "device": "bus01",
        "event_type": "entry",
        "count": 1,
        "uptime_ms": 5000,
    })
    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    assert "server_time" in response.json()


def test_post_event_missing_required_field(client):
    # Pydantic should reject this with 422 Unprocessable Entity
    response = client.post("/events", json={"device": "bus01"})
    assert response.status_code == 422


def test_post_event_exit(client):
    response = client.post("/events", json={
        "device": "bus01",
        "event_type": "exit",
        "count": 0,
        "uptime_ms": 9000,
    })
    assert response.status_code == 200


def test_recent_events_empty_at_start(client):
    response = client.get("/events/recent")
    assert response.status_code == 200
    assert response.json()["count"] == 0
    assert response.json()["events"] == []


def test_recent_events_shows_posted_event(client):
    client.post("/events", json={
        "device": "bus01",
        "event_type": "entry",
        "count": 2,
        "uptime_ms": 1000,
    })
    response = client.get("/events/recent")
    assert response.json()["count"] == 1
    assert response.json()["events"][0]["event_type"] == "entry"
    assert response.json()["events"][0]["count_after"] == 2


def test_recent_events_limit_parameter(client):
    # Post 5 events, request only 3 back
    for i in range(1, 6):
        client.post("/events", json={
            "device": "bus01",
            "event_type": "entry",
            "count": i,
            "uptime_ms": i * 1000,
        })
    response = client.get("/events/recent?limit=3")
    assert response.json()["count"] == 3


def test_recent_events_ordered_newest_first(client):
    for i in range(1, 4):
        client.post("/events", json={
            "device": "bus01",
            "event_type": "entry",
            "count": i,
            "uptime_ms": i * 1000,
        })
    events = client.get("/events/recent").json()["events"]
    # newest first means count_after should go 3, 2, 1
    counts = [e["count_after"] for e in events]
    assert counts == sorted(counts, reverse=True)
