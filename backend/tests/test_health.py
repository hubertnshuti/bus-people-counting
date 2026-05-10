"""
Health endpoint tests — the simplest possible tests.
A good place to see the pattern: call an endpoint, check the response.
"""


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_health_body(client):
    response = client.get("/health")
    data = response.json()
    assert data["status"] == "ok"
    assert "time" in data  # timestamp must be present
