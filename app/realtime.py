from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any

from fastapi import WebSocket


class PipelineEventHub:
    """Process-local fan-out for one backend worker.

    The production container runs one Uvicorn worker. If that changes, replace
    this hub with Redis pub/sub while keeping the WebSocket payload contract.
    """

    def __init__(self, backlog_size: int = 5000) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._log_backlog: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=backlog_size)
        )
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        job_id: str,
        websocket: WebSocket,
        *,
        fallback_lines: list[str] | None = None,
    ) -> None:
        # Holding the lock while sending the snapshot guarantees that a live
        # event cannot overtake the snapshot on this connection.
        async with self._lock:
            backlog = list(self._log_backlog.get(job_id, ()))
            if backlog:
                lines = [
                    f"[{event.get('step_name', 'unknown')}.log] {event.get('message', '')}"
                    for event in backlog
                ]
            else:
                lines = fallback_lines or []
            await websocket.send_json(
                {
                    "schema_version": 1,
                    "type": "log_snapshot",
                    "job_id": job_id,
                    "lines": lines,
                    "events": backlog,
                }
            )
            self._connections[job_id].add(websocket)

    async def unsubscribe(self, job_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            connections = self._connections.get(job_id)
            if connections is None:
                return
            connections.discard(websocket)
            if not connections:
                self._connections.pop(job_id, None)

    async def publish(self, job_id: str, payload: dict[str, Any]) -> None:
        if payload.get("type") == "log_batch":
            for event in payload.get("events") or []:
                if isinstance(event, dict):
                    self._log_backlog[job_id].append(event)

        async with self._lock:
            connections = tuple(self._connections.get(job_id, ()))

        stale: list[WebSocket] = []
        for websocket in connections:
            try:
                await websocket.send_json(payload)
            except Exception:  # noqa: BLE001
                stale.append(websocket)
        for websocket in stale:
            await self.unsubscribe(job_id, websocket)

    async def clear(self, job_id: str) -> None:
        async with self._lock:
            self._log_backlog.pop(job_id, None)


pipeline_event_hub = PipelineEventHub()
