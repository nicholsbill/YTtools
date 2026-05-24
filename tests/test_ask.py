# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the Ask tool. Embedding and answer providers are faked; no network."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from yttools.config import Settings
from yttools.core.db import Database
from yttools.core.models import Channel, Segment, Transcript, Video
from yttools.tools.ask import AskError, ask_question, embedding_provider, index_channel


class _FakeEmbed:
    """Maps keywords to a small fixed vector so retrieval is deterministic."""

    name = "emb"
    default_model = "e"

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        vectors = []
        for text in texts:
            low = text.lower()
            vectors.append(
                [
                    1.0 if any(w in low for w in ("machine", "learning", "neural")) else 0.0,
                    1.0 if any(w in low for w in ("rust", "ownership", "borrow")) else 0.0,
                    1.0,  # constant dim keeps every vector non-zero
                ]
            )
        return vectors


class _FakeAnswer:
    name = "ans"
    default_model = "m"

    def __init__(self, text: str) -> None:
        self.text = text

    async def complete(self, prompt: str, **kwargs: object) -> str:
        return self.text


def _seed(db: Database) -> None:
    db.upsert_channel(Channel(id="UCA", title="Alpha"))
    rows = [
        ("v1", "machine learning and neural networks predict outcomes"),
        ("v2", "rust ownership and borrowing keep memory safe"),
    ]
    for vid, text in rows:
        db.upsert_video(
            Video(
                id=vid, channel_id="UCA", title=vid, published_at=datetime(2024, 1, 1, tzinfo=UTC)
            )
        )
        db.upsert_transcript(
            Transcript(
                video_id=vid,
                language="en",
                is_auto_generated=True,
                text=text,
                segments=[Segment(start=0.0, end=5.0, text=text)],
            )
        )


async def test_index_then_ask_cites_relevant_video(db: Database) -> None:
    _seed(db)
    embed = _FakeEmbed()
    indexed = await index_channel(db, embed, "UCA")
    assert indexed.videos_indexed == 2
    assert indexed.chunks_indexed >= 2
    assert db.count_chunk_embeddings(["UCA"]) >= 2

    result = await ask_question(
        db,
        embed,
        _FakeAnswer("The channel covers ML [1]."),
        "what about machine learning?",
        channel_ids=["UCA"],
    )
    assert result.citations
    # The machine-learning chunk should rank first for this question.
    assert result.citations[0].video_id == "v1"
    # The [1] marker is rewritten into a Markdown link to the source moment.
    assert "](https://www.youtube.com/watch?v=v1" in result.answer


async def test_reindex_skips_unless_forced(db: Database) -> None:
    _seed(db)
    embed = _FakeEmbed()
    await index_channel(db, embed, "UCA")
    again = await index_channel(db, embed, "UCA")
    assert again.videos_indexed == 0
    forced = await index_channel(db, embed, "UCA", force=True)
    assert forced.videos_indexed == 2


async def test_ask_without_index_raises(db: Database) -> None:
    _seed(db)
    with pytest.raises(AskError):
        await ask_question(db, _FakeEmbed(), _FakeAnswer("x"), "q", channel_ids=["UCA"])


async def test_index_unknown_channel_raises(db: Database) -> None:
    with pytest.raises(AskError):
        await index_channel(db, _FakeEmbed(), "missing")


def test_embedding_provider_is_local_ollama() -> None:
    assert embedding_provider(Settings()).name == "ollama"
