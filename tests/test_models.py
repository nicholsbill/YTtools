# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the Pydantic domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from yttools.core.models import (
    Chapter,
    Quote,
    Segment,
    VideoMetadata,
)


def test_video_metadata_to_video_and_channel() -> None:
    meta = VideoMetadata(
        id="abc12345678",
        title="Example",
        channel_id="UCsT0YIqwnpJCM-mx7-gSA4Q",
        channel_title="Example Channel",
        channel_handle="@example",
        duration_seconds=600,
        chapters=[Chapter(start=0.0, title="Intro")],
        tags=["a", "b"],
    )
    video = meta.to_video()
    assert video.id == "abc12345678"
    assert video.channel_id == "UCsT0YIqwnpJCM-mx7-gSA4Q"
    assert video.chapters[0].title == "Intro"
    assert video.tags == ["a", "b"]

    channel = meta.to_channel()
    assert channel is not None
    assert channel.title == "Example Channel"
    assert channel.handle == "@example"


def test_video_metadata_to_channel_none_without_channel_id() -> None:
    meta = VideoMetadata(id="abc12345678", title="Example")
    assert meta.to_channel() is None


def test_segment_fields() -> None:
    segment = Segment(start=1.5, end=3.0, text="hello")
    assert segment.end > segment.start


def test_quote_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        Quote(video_id="abc12345678", text="x", quote_type="opinion")  # type: ignore[arg-type]


def test_quote_accepts_known_type() -> None:
    quote = Quote(video_id="abc12345678", text="x", quote_type="prediction")
    assert quote.quote_type == "prediction"
    assert quote.id is None


def test_video_defaults_empty_collections() -> None:
    meta = VideoMetadata(id="abc12345678", title="t")
    video = meta.to_video()
    assert video.chapters == []
    assert video.tags == []
