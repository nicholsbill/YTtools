# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the Summarize tool. The LLM provider is faked; no network access."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from yttools.core.db import Database
from yttools.core.models import Channel, Segment, Transcript, Video
from yttools.tools.summarize import SummarizeError, summarize_channel


class _FakeProvider:
    """Returns canned responses keyed off the prompt; counts calls."""

    name = "fake"
    default_model = "fake-1"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, prompt: str, **kwargs: object) -> str:
        self.calls += 1
        if '"topics"' in prompt:
            return '{"topics": ["Machine Learning", "Vector Search"]}'
        if '"guests"' in prompt:
            return '{"guests": [{"name": "Ada Lovelace", "background": "mathematician"}]}'
        return "These videos cover machine learning and search."


def _seed(db: Database, count: int = 3) -> None:
    db.upsert_channel(Channel(id="UC1", title="Chan"))
    for i in range(count):
        vid = f"vid{i:08d}00"
        db.upsert_video(
            Video(
                id=vid,
                channel_id="UC1",
                title=f"Video {i}",
                duration_seconds=600,
                published_at=datetime(2024, 1, 1 + i, tzinfo=UTC),
            )
        )
        db.upsert_transcript(
            Transcript(
                video_id=vid,
                language="en",
                is_auto_generated=True,
                text="machine learning and vector search " * 20,
                segments=[Segment(start=0.0, end=5.0, text="machine learning and vector search")],
            )
        )


async def test_cadence_uses_no_llm(db: Database) -> None:
    _seed(db)
    provider = _FakeProvider()
    result = await summarize_channel(db, provider, "UC1", summary_types=["cadence"])
    assert provider.calls == 0
    assert "Cadence" in result.sections[0].content
    assert "videos/month" in result.sections[0].content


async def test_topics_are_clustered_and_persisted(db: Database) -> None:
    _seed(db)
    result = await summarize_channel(db, _FakeProvider(), "UC1", summary_types=["topics"])
    content = result.sections[0].content
    assert "Machine Learning" in content
    topics = db.list_topics("UC1")
    assert topics
    assert any("Machine" in t.label for t in topics)
    # Each topic links its videos for Compare/Timeline to reuse.
    assert db.list_video_topics("UC1")


async def test_overview_and_guests(db: Database) -> None:
    _seed(db)
    result = await summarize_channel(
        db, _FakeProvider(), "UC1", summary_types=["overview", "guests"]
    )
    by_type = {s.summary_type: s.content for s in result.sections}
    assert by_type["overview"].startswith("## Overview")
    assert "Ada Lovelace" in by_type["guests"]


async def test_results_are_cached_unless_forced(db: Database) -> None:
    _seed(db)
    provider = _FakeProvider()
    await summarize_channel(db, provider, "UC1", summary_types=["overview"])
    after_first = provider.calls
    assert after_first > 0
    await summarize_channel(db, provider, "UC1", summary_types=["overview"])
    assert provider.calls == after_first  # served from cache
    await summarize_channel(db, provider, "UC1", summary_types=["overview"], force=True)
    assert provider.calls > after_first


async def test_unknown_channel_raises(db: Database) -> None:
    with pytest.raises(SummarizeError):
        await summarize_channel(db, _FakeProvider(), "missing", summary_types=["cadence"])
