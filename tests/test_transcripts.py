# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for VTT parsing, cleaning, and offset-to-segment mapping."""

from __future__ import annotations

from pathlib import Path

from yttools.core.models import Segment
from yttools.core.transcripts import (
    clean_text,
    parse_vtt,
    segment_at_offset,
    timestamp_to_seconds,
)


def test_timestamp_to_seconds() -> None:
    assert timestamp_to_seconds("00:00:03.000") == 3.0
    assert timestamp_to_seconds("01:02:03.500") == 3723.5
    assert timestamp_to_seconds("02:05.250") == 125.25
    assert timestamp_to_seconds("00:00:04,000") == 4.0  # comma decimal separator


def test_clean_text_strips_and_dedupes() -> None:
    raw = ">> Hello there\n>> Hello there\n[Music]\nJANE: a real line\n"
    cleaned = clean_text(raw)
    assert cleaned == "Hello there a real line"


def test_parse_vtt_dedupes_rolling_captions(fixtures_dir: Path) -> None:
    result = parse_vtt(fixtures_dir / "sample.vtt")
    texts = [segment.text for segment in result.segments]
    assert texts == [
        "Welcome to the show",
        "today we talk about machine learning",
        "and how it changes research",
        "vector search is the next frontier",
    ]
    assert "[Music]" not in result.text
    assert "JANE:" not in result.text
    assert ">>" not in result.text
    assert result.word_count == len(result.text.split())


def test_parse_vtt_timestamps_are_ordered(fixtures_dir: Path) -> None:
    result = parse_vtt(fixtures_dir / "sample.vtt")
    starts = [segment.start for segment in result.segments]
    assert starts == sorted(starts)
    assert result.segments[0].start == 0.0
    assert result.segments[-1].text == "vector search is the next frontier"


def test_text_is_join_of_segments(fixtures_dir: Path) -> None:
    result = parse_vtt(fixtures_dir / "sample.vtt")
    assert result.text == " ".join(segment.text for segment in result.segments)


def test_segment_at_offset_maps_to_correct_segment(fixtures_dir: Path) -> None:
    result = parse_vtt(fixtures_dir / "sample.vtt")
    # Offset of the word "vector" in the joined text should map to the last segment.
    offset = result.text.index("vector")
    segment = segment_at_offset(result.segments, offset)
    assert segment is not None
    assert segment.text == "vector search is the next frontier"
    assert segment.start == 13.0

    # Offset 0 maps to the first segment.
    first = segment_at_offset(result.segments, 0)
    assert first is not None
    assert first.start == 0.0


def test_segment_at_offset_empty() -> None:
    assert segment_at_offset([], 5) is None


def test_segment_at_offset_beyond_end_returns_last() -> None:
    segments = [
        Segment(start=0.0, end=1.0, text="hello"),
        Segment(start=1.0, end=2.0, text="world"),
    ]
    assert segment_at_offset(segments, 10_000) is segments[-1]
