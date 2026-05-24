# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Transcript chunking and embedding helpers for the Ask tool.

v0.1.0 ships the chunking logic and the embedding call, both pure and testable.
Persisting embeddings into the ``chunks`` vector table is wired up in v0.3.0 when
the Ask tool lands, so :func:`index_transcript` is intentionally a stub.
"""

from __future__ import annotations

from pydantic import BaseModel

from yttools.core.llm import EMBEDDING_DIMENSIONS, LLMProvider
from yttools.core.models import Segment

DEFAULT_TARGET_WORDS = 500
DEFAULT_OVERLAP_WORDS = 100


class Chunk(BaseModel):
    """A windowed slice of a transcript ready for embedding."""

    chunk_index: int
    text: str
    start_seconds: float


def chunk_transcript(
    segments: list[Segment],
    *,
    target_words: int = DEFAULT_TARGET_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
) -> list[Chunk]:
    """Split timed segments into overlapping word windows.

    Each chunk carries the start time of its first word so answers can deep-link
    back to the right moment.
    """
    if target_words <= 0:
        raise ValueError("target_words must be positive")
    overlap = max(0, min(overlap_words, target_words - 1))

    timed_words: list[tuple[str, float]] = []
    for segment in segments:
        for word in segment.text.split():
            timed_words.append((word, segment.start))

    chunks: list[Chunk] = []
    step = target_words - overlap
    index = 0
    position = 0
    while position < len(timed_words):
        window = timed_words[position : position + target_words]
        if not window:
            break
        text = " ".join(word for word, _ in window)
        chunks.append(Chunk(chunk_index=index, text=text, start_seconds=window[0][1]))
        index += 1
        if position + target_words >= len(timed_words):
            break
        position += step
    return chunks


async def embed_chunks(provider: LLMProvider, chunks: list[Chunk]) -> list[list[float]]:
    """Embed each chunk's text through the configured provider."""
    if not chunks:
        return []
    return await provider.embed([chunk.text for chunk in chunks])


__all__ = [
    "DEFAULT_OVERLAP_WORDS",
    "DEFAULT_TARGET_WORDS",
    "EMBEDDING_DIMENSIONS",
    "Chunk",
    "chunk_transcript",
    "embed_chunks",
]
