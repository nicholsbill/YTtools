# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Server-Sent Events for live job progress."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from yttools.core.progress import ProgressBus

router = APIRouter(prefix="/api")


@router.get("/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str) -> EventSourceResponse:
    """Stream progress events for a job until it reaches a terminal state."""
    bus: ProgressBus = request.app.state.bus

    async def event_source() -> AsyncIterator[dict[str, str]]:
        async for event in bus.stream(job_id):
            if await request.is_disconnected():
                break
            yield {"event": event.event, "data": event.model_dump_json()}

    return EventSourceResponse(event_source())
