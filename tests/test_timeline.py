# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the Timeline tool. Topics are pre-seeded; no LLM call is needed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from yttools.core.db import Database
from yttools.core.models import Channel, Topic, Transcript, Video
from yttools.tools.timeline import TimelineError, build_timeline


class _NoCallProvider:
    name = "fake"
    default_model = "m"

    async def complete(self, prompt: str, **kwargs: object) -> str:
        return "{}"


def _seed(db: Database) -> None:
    db.upsert_channel(Channel(id="UCA", title="Alpha"))
    rows = [
        (
            "v1",
            "Machine Learning",
            "machine learning and neural networks",
            datetime(2024, 1, 5, tzinfo=UTC),
        ),
        (
            "v2",
            "Machine Learning",
            "more machine learning content",
            datetime(2024, 2, 9, tzinfo=UTC),
        ),
        ("v3", "Rust", "rust ownership and borrowing", datetime(2024, 2, 20, tzinfo=UTC)),
    ]
    for vid, label, text, published in rows:
        db.upsert_video(Video(id=vid, channel_id="UCA", title=vid, published_at=published))
        db.upsert_transcript(
            Transcript(video_id=vid, language="en", is_auto_generated=True, text=text, segments=[])
        )
        topic_id = db.add_topic(Topic(channel_id="UCA", label=label, video_count=1))
        db.add_video_topic(vid, topic_id)


async def test_auto_mode_buckets_by_month(db: Database) -> None:
    _seed(db)
    result = await build_timeline(db, _NoCallProvider(), "UCA", mode="auto")
    assert result.mode == "auto"
    assert "2024-01" in result.months and "2024-02" in result.months
    labels = {s.topic for s in result.series}
    assert "Machine Learning" in labels
    ml = next(s for s in result.stats if s.topic == "Machine Learning")
    assert ml.total == 2


async def test_specific_mode_matches_transcripts(db: Database) -> None:
    db.upsert_channel(Channel(id="UCA", title="Alpha"))
    db.upsert_video(
        Video(id="v1", channel_id="UCA", title="t", published_at=datetime(2024, 3, 1, tzinfo=UTC))
    )
    db.upsert_transcript(
        Transcript(
            video_id="v1",
            language="en",
            is_auto_generated=True,
            text="we explore machine learning and neural networks",
            segments=[],
        )
    )
    result = await build_timeline(
        db, _NoCallProvider(), "UCA", mode="specific", topics=["machine learning", "cooking"]
    )
    assert result.mode == "specific"
    series = {s.topic: s for s in result.series}
    assert sum(series["machine learning"].counts) == 1
    assert sum(series["cooking"].counts) == 0


async def test_unknown_channel_raises(db: Database) -> None:
    with pytest.raises(TimelineError):
        await build_timeline(db, _NoCallProvider(), "missing")
