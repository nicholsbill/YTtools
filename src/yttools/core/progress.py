# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""In-process publish/subscribe bus for streaming job progress over SSE.

Each job id owns a topic. Publishers push :class:`ProgressEvent` objects; the web
SSE route subscribes and forwards them to the browser. The bus lives in the same
process as the worker pool, so no broker or external service is involved.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

_TERMINAL_EVENTS = {"job_done", "job_error", "job_cancelled"}


class ProgressEvent(BaseModel):
    """A single progress update for a job."""

    job_id: str
    event: str
    message: str = ""
    current: int = 0
    total: int = 0
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.event in _TERMINAL_EVENTS


class ProgressBus:
    """Fan-out of progress events to per-job subscriber queues."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[ProgressEvent | None]]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, event: ProgressEvent) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(event.job_id, []))
        for queue in queues:
            await queue.put(event)
            if event.is_terminal:
                await queue.put(None)

    async def subscribe(self, job_id: str) -> asyncio.Queue[ProgressEvent | None]:
        queue: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()
        async with self._lock:
            self._subscribers.setdefault(job_id, []).append(queue)
        return queue

    async def unsubscribe(self, job_id: str, queue: asyncio.Queue[ProgressEvent | None]) -> None:
        async with self._lock:
            queues = self._subscribers.get(job_id)
            if not queues:
                return
            if queue in queues:
                queues.remove(queue)
            if not queues:
                self._subscribers.pop(job_id, None)

    async def stream(self, job_id: str) -> AsyncIterator[ProgressEvent]:
        """Yield events for a job until a terminal event closes the stream."""
        queue = await self.subscribe(job_id)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            await self.unsubscribe(job_id, queue)


_DEFAULT_BUS = ProgressBus()


def get_bus() -> ProgressBus:
    """Return the process-wide progress bus."""
    return _DEFAULT_BUS
