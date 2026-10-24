from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from ghostos.config import Settings
from ghostos.core.events import Emitter, EventBus
from ghostos.core.types import Event

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(home=tmp_path / "home", provider="offline", sandbox="process")


@pytest.fixture
def recorder() -> tuple[Emitter, list[Event]]:
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe_sync(events.append)
    return Emitter(bus, "run_test"), events


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def demo_server() -> Iterator[str]:
    """Serve the bundled demo site (no dashboard, isolated memory) on a free port."""
    import tempfile

    from ghostos.runtime import GhostOS
    from ghostos.server.app import create_app

    home = Path(tempfile.mkdtemp(prefix="ghostos-server-"))
    app = create_app(GhostOS(Settings(home=home, provider="offline", sandbox="process")))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)
