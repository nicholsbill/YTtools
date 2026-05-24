# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for transcript chunking and embedding helpers."""

from __future__ import annotations

import pytest

from yttools.core.embeddings import chunk_transcript, embed_chunks, index_transcript
from yttools.core.models import Segment


def _segments(total_words: int, words_per_segment: int = 10) -> list[Segment]:
    segments: list[Segment] = []
    start = 0.0
    for index in range(0, total_words, words_per_segment):
        count = min(words_per_segment, total_words - index)
        text = " ".join(f"w{index + offset}" for offset in range(count))
        segments.append(Segment(start=start, end=start + 5.0, text=text))
        start += 5.0
    return segments


def test_chunk_transcript_respects_target_and_overlap() -> None:
    segments = _segments(1000, words_per_segment=10)
    chunks = chunk_transcript(segments, target_words=500, overlap_words=100)
    assert len(chunks) >= 2
    assert all(len(chunk.text.split()) <= 500 for chunk in chunks)
    # Overlap means the second chunk starts before the first one ends.
    assert chunks[1].chunk_index == 1
    first_words = chunks[0].text.split()
    second_words = chunks[1].text.split()
    assert first_words[-100:] == second_words[:100]


def test_chunk_transcript_assigns_timestamps() -> None:
    segments = _segments(40, words_per_segment=10)
    chunks = chunk_transcript(segments, target_words=15, overlap_words=5)
    assert chunks[0].start_seconds == 0.0
    assert all(chunk.start_seconds >= 0.0 for chunk in chunks)


def test_chunk_transcript_short_input_single_chunk() -> None:
    chunks = chunk_transcript(_segments(20), target_words=500, overlap_words=100)
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0


def test_chunk_transcript_empty() -> None:
    assert chunk_transcript([]) == []


def test_chunk_transcript_rejects_bad_target() -> None:
    with pytest.raises(ValueError, match="target_words"):
        chunk_transcript(_segments(10), target_words=0)


async def test_embed_chunks_uses_provider() -> None:
    class FakeProvider:
        name = "fake"
        default_model = "m"

        async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
            return [[float(len(text))] for text in texts]

    chunks = chunk_transcript(_segments(30), target_words=10, overlap_words=2)
    vectors = await embed_chunks(FakeProvider(), chunks)  # type: ignore[arg-type]
    assert len(vectors) == len(chunks)


async def test_embed_chunks_empty_returns_empty() -> None:
    class FakeProvider:
        name = "fake"
        default_model = "m"

        async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
            raise AssertionError("should not be called for empty input")

    assert await embed_chunks(FakeProvider(), []) == []  # type: ignore[arg-type]


def test_index_transcript_is_stubbed() -> None:
    with pytest.raises(NotImplementedError, match=r"v0\.3\.0"):
        index_transcript()
