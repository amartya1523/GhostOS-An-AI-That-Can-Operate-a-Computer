from fastapi.testclient import TestClient

from ghostos.runtime import GhostOS
from ghostos.server.app import create_app


def test_api_basics(settings) -> None:
    client = TestClient(create_app(GhostOS(settings)))
    assert client.get("/api/health").json()["ok"]
    assert "Sign in to Acme" in client.get("/sites/acme/").text
    assert client.post("/sites/acme/deploy", json={"version": "v2"}).json() == {"version": "v2"}
    assert "Welcome back" in client.get("/sites/acme/").text
    assert client.post("/sites/acme/deploy", json={"version": "v9"}).status_code == 400
    assert client.post("/api/approvals/nope", json={"approved": True}).status_code == 404
    assert client.get("/api/runs/missing").status_code == 404
    assert client.get("/api/artifact", params={"path": "/etc/passwd"}).status_code == 404
