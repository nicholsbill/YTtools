# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the in-process progress bus."""

from __future__ import annotations

from yttools.core.progress import ProgressBus, ProgressEvent, get_bus


async def test_subscribe_receives_published_event() -> None:
    bus = ProgressBus()
    queue = await bus.subscribe("job-1")
    await bus.publish(ProgressEvent(job_id="job-1", event="progress", current=1, total=3))
    event = await queue.get()
    assert event is not None
    assert event.current == 1


async def test_other_jobs_do_not_receive_events() -> None:
    bus = ProgressBus()
    queue = await bus.subscribe("job-1")
    await bus.publish(ProgressEvent(job_id="job-2", event="progress"))
    assert queue.empty()


async def test_stream_stops_on_terminal_event() -> None:
    bus = ProgressBus()

    async def collect() -> list[str]:
        return [event.event async for event in bus.stream("job-1")]

    import asyncio

    task = asyncio.ensure_future(collect())
    await asyncio.sleep(0)  # let the subscriber register
    await bus.publish(ProgressEvent(job_id="job-1", event="progress"))
    await bus.publish(ProgressEvent(job_id="job-1", event="job_done"))
    events = await task
    assert events == ["progress", "job_done"]


async def test_unsubscribe_cleans_up() -> None:
    bus = ProgressBus()
    queue = await bus.subscribe("job-1")
    await bus.unsubscribe("job-1", queue)
    # Publishing to a job with no subscribers is a no-op.
    await bus.publish(ProgressEvent(job_id="job-1", event="progress"))


def test_terminal_flag() -> None:
    assert ProgressEvent(job_id="x", event="job_done").is_terminal is True
    assert ProgressEvent(job_id="x", event="progress").is_terminal is False


def test_get_bus_is_singleton() -> None:
    assert get_bus() is get_bus()
