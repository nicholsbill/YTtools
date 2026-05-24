# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the Compare tool. Topics are pre-seeded so no LLM call is needed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from yttools.core.db import Database
from yttools.core.models import Channel, Topic, Transcript, Video
from yttools.tools.compare import CompareError, compare_channels


class _NoCallProvider:
    name = "fake"
    default_model = "m"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, prompt: str, **kwargs: object) -> str:
        self.calls += 1
        return "{}"


def _seed_channel(db: Database, cid: str, title: str, items: list[tuple[str, str]]) -> None:
    db.upsert_channel(Channel(id=cid, title=title))
    for i, (label, text) in enumerate(items):
        vid = f"{cid}_v{i}"
        db.upsert_video(
            Video(
                id=vid,
                channel_id=cid,
                title=f"{title} {i}",
                published_at=datetime(2024, 1, 1 + i, tzinfo=UTC),
            )
        )
        db.upsert_transcript(
            Transcript(video_id=vid, language="en", is_auto_generated=True, text=text, segments=[])
        )
        topic_id = db.add_topic(Topic(channel_id=cid, label=label, video_count=1))
        db.add_video_topic(vid, topic_id)


async def test_overlap_vocabulary_and_timing(db: Database) -> None:
    _seed_channel(
        db,
        "UCA",
        "Alpha",
        [
            ("Machine Learning", "neural networks deep learning models training data"),
            ("Rust", "rust borrow checker ownership lifetimes systems"),
        ],
    )
    _seed_channel(
        db,
        "UCB",
        "Beta",
        [
            ("Machine Learning", "neural networks deep learning models"),
            ("Cooking", "recipes cooking baking kitchen ingredients"),
        ],
    )
    provider = _NoCallProvider()
    result = await compare_channels(db, provider, ["UCA", "UCB"])

    assert provider.calls == 0  # topics already present, no extraction
    assert "Machine Learning" in {s.label for s in result.shared_topics}
    assert "Rust" in result.unique_topics["UCA"]
    assert "Cooking" in result.unique_topics["UCB"]
    assert result.vocabulary["UCA"] and result.vocabulary["UCB"]
    assert any(row.label == "Machine Learning" for row in result.timing)


async def test_requires_two_to_five(db: Database) -> None:
    _seed_channel(db, "UCA", "Alpha", [("X", "some text here")])
    with pytest.raises(CompareError):
        await compare_channels(db, _NoCallProvider(), ["UCA"])


async def test_unknown_channel_raises(db: Database) -> None:
    _seed_channel(db, "UCA", "Alpha", [("X", "some text here")])
    with pytest.raises(CompareError):
        await compare_channels(db, _NoCallProvider(), ["UCA", "UCmissing"])
