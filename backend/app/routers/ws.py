from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..events import bus

router = APIRouter()


@router.websocket("/events")
async def events(websocket: WebSocket) -> None:
    """Push stream for the dashboard.

    Sends a `ready` frame with the recent backlog on connect, so a dashboard
    that reloads mid-demo catches up instead of showing an empty feed. After
    that, one JSON frame per WSEvent. A ping every 20s keeps proxies from
    dropping an idle connection during the pitch.
    """
    await websocket.accept()
    queue = bus.subscribe()
    try:
        await websocket.send_json(
            {
                "type": "ready",
                "backlog": [e.model_dump(mode="json") for e in bus.recent(25)],
            }
        )
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(queue)
