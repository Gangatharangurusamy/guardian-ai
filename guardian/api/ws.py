"""GUARDIAN WebSocket manager.

Provides a ConnectionManager singleton that the FastAPI lifespan registers
as the post-write callback in guardian.store.writer._post_write_callback.

On each new connection:
  1. Sends the last 10 traces immediately as a "snapshot" message.
  2. Streams live events as they are written to the DB via the callback.

A 30-second keepalive ping is sent to all connections to prevent proxy timeouts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("guardian")


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts trace events."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        self._keepalive_task: asyncio.Task[None] | None = None

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection and send an initial snapshot."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.debug("GUARDIAN WS: client connected (%d total)", len(self.active_connections))

        # Send last 10 traces as initial snapshot
        try:
            from guardian.store.reader import list_traces
            snapshot = list_traces(limit=10)
            await websocket.send_text(json.dumps({
                "type": "snapshot",
                "data": snapshot,
            }, default=str))
        except Exception as exc:
            logger.warning("GUARDIAN WS: snapshot failed: %s", exc)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the active list."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.debug("GUARDIAN WS: client disconnected (%d total)", len(self.active_connections))

    async def broadcast(self, trace_event: dict[str, Any]) -> None:
        """Broadcast a trace event to all active WebSocket connections.

        Dead connections are silently removed. Never raises.
        """
        if not self.active_connections:
            return

        message = json.dumps({
            "type": "trace",
            "data": trace_event,
        }, default=str)

        dead: list[WebSocket] = []
        for ws in list(self.active_connections):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

    async def start_keepalive(self) -> None:
        """Start the 30-second ping keepalive task."""
        if self._keepalive_task is None or self._keepalive_task.done():
            self._keepalive_task = asyncio.create_task(self._ping_loop())

    async def stop_keepalive(self) -> None:
        """Cancel the keepalive task."""
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass

    async def _ping_loop(self) -> None:
        """Send a ping to all connections every 30 seconds."""
        try:
            while True:
                await asyncio.sleep(30)
                if self.active_connections:
                    dead: list[WebSocket] = []
                    for ws in list(self.active_connections):
                        try:
                            await ws.send_text(json.dumps({"type": "ping"}))
                        except Exception:
                            dead.append(ws)
                    for ws in dead:
                        self.disconnect(ws)
        except asyncio.CancelledError:
            pass


# Module-level singleton — imported by main.py to register the callback
manager = ConnectionManager()


async def ws_traces_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint handler for /ws/traces.

    Registered in main.py as:
        @app.websocket("/ws/traces")
        async def ws_traces(ws: WebSocket):
            await ws_traces_endpoint(ws)
    """
    await manager.connect(websocket)
    try:
        while True:
            # Keep the connection alive; broadcast is driven by the callback
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:
        logger.warning("GUARDIAN WS: connection error: %s", exc)
        manager.disconnect(websocket)
