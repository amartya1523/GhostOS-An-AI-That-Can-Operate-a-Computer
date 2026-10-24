from fastapi.testclient import TestClient

from ghostos.runtime import GhostOS
from ghostos.server.app import create_app


def test_api_basics(settings) -> None:
    client = TestClient(create_app(GhostOS(settings)))
    assert client.get("/api/health").json()["ok"]
    assert "Sign in to Acme" in client.get("/sites/acme/").text
    assert client.post(
        "/sites/acme/deploy", json={"version": "v2"}, headers={"x-ghostos-client": "test"}
    ).json() == {"version": "v2"}
    assert "Welcome back" in client.get("/sites/acme/").text
    assert (
        client.post(
            "/sites/acme/deploy", json={"version": "v9"}, headers={"x-ghostos-client": "test"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/approvals/nope", json={"approved": True}, headers={"x-ghostos-client": "test"}
        ).status_code
        == 404
    )
    assert client.get("/api/runs/missing").status_code == 404
    assert client.get("/api/artifact", params={"path": "/etc/passwd"}).status_code == 404


def test_artifacts_are_served_only_from_home(settings) -> None:
    ghost = GhostOS(settings)
    shot = ghost.settings.runs_dir / "r" / "shot.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG fake")
    client = TestClient(create_app(ghost))
    assert client.get("/api/artifact", params={"path": str(shot)}).status_code == 200
    escape = str(ghost.settings.runs_dir / ".." / ".." / ".." / "etc" / "passwd")
    assert client.get("/api/artifact", params={"path": escape}).status_code == 404


def test_cross_site_requests_are_refused(settings) -> None:
    client = TestClient(create_app(GhostOS(settings)))
    # no custom header → a page on another origin could have sent this
    assert client.post("/api/runs", json={"task": "run sh -c id"}).status_code == 403
    assert client.post("/api/approvals/x", json={"approved": True}).status_code == 403
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/stream", headers={"origin": "https://evil.example"}) as ws,
    ):
        ws.receive_json()


def test_memory_database_is_not_downloadable(settings) -> None:
    ghost = GhostOS(settings)
    client = TestClient(create_app(ghost))
    assert (
        client.get("/api/artifact", params={"path": str(ghost.settings.db_path)}).status_code == 404
    )
