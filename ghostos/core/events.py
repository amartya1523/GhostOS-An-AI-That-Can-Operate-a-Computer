"""In-process pub/sub that fans agent events out to the dashboard, trace files and memory."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from ghostos.core.types import Event

Listener = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[Event]] = set()
        self._listeners: list[Listener] = []

    def subscribe_sync(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def unsubscribe_sync(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def publish(self, event: Event) -> None:
        for listener in self._listeners:
            listener(event)
        for queue in self._queues:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    async def stream(self) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        self._queues.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._queues.discard(queue)


class Emitter:
    """A run-scoped handle agents use to publish events without knowing the bus."""

    def __init__(self, bus: EventBus, run_id: str) -> None:
        self.bus = bus
        self.run_id = run_id

    def __call__(self, type_: str, message: str = "", agent: str = "system", **data: Any) -> Event:
        event = Event(run_id=self.run_id, type=type_, agent=agent, message=message, data=data)
        self.bus.publish(event)
        return event


class JsonlTrace:
    """Appends every event of a run to ``trace.jsonl`` so runs can be replayed offline."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, event: Event) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.model_dump(), default=str) + "\n")
