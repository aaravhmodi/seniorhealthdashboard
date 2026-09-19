"""Fan-out for the /events WebSocket.

Every mutation goes through publish(), so the dashboard never has to poll.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .schemas import EventType, WSEvent
from .store import now


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[WSEvent]] = set()
        self._recent: list[WSEvent] = []

    def subscribe(self) -> asyncio.Queue[WSEvent]:
        q: asyncio.Queue[WSEvent] = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[WSEvent]) -> None:
        self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def recent(self, limit: int = 25) -> list[WSEvent]:
        return self._recent[-limit:]

    def publish(
        self, type_: EventType, senior_id: str, payload: dict[str, Any] | None = None
    ) -> WSEvent:
        event = WSEvent(type=type_, at=now(), senior_id=senior_id, payload=payload or {})
        self._recent.append(event)
        del self._recent[:-200]
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled dashboard must never block a check-in.
                self._subscribers.discard(q)
        return event


bus = EventBus()
