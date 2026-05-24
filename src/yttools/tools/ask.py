# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Ask: retrieval-augmented question answering over a channel.

Indexing chunks each transcript, embeds the chunks (locally via Ollama by
default, independent of the answer model), and stores them in
``chunk_embeddings``. A query embeds the question, retrieves the nearest chunks
by cosine similarity, reranks by similarity and recency, and asks the answer
model for a cited response whose ``[n]`` markers are rewritten into links back
to the source moments.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from yttools.config import Settings
from yttools.core.db import Database
from yttools.core.embeddings import chunk_transcript, embed_chunks
from yttools.core.exports import format_clock, watch_url
from yttools.core.llm import LLMError, LLMProvider, OllamaProvider
from yttools.core.progress import ProgressCallback, report

_TOP_K = 20
_TOP_N = 8
_SIM_WEIGHT = 0.7
_RECENCY_WEIGHT = 0.3
_SNIPPET_CHARS = 160


class AskError(RuntimeError):
    """Raised when indexing or answering cannot proceed."""


class Citation(BaseModel):
    index: int
    video_id: str
    title: str
    start_seconds: float
    url: str
    snippet: str


class AskResult(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)


class IndexResult(BaseModel):
    channel_id: str
    videos_indexed: int = 0
    chunks_indexed: int = 0
    total_chunks: int = 0


def embedding_provider(settings: Settings) -> LLMProvider:
    """Build the local Ollama provider used for embeddings (per the spec)."""
    return OllamaProvider(
        base_url=settings.llm.ollama.base_url,
        default_model=settings.llm.default_model,
        embedding_model=settings.llm.embedding_model,
        concurrency=settings.llm.concurrent_requests,
    )


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _days_since(published_at: object) -> float | None:
    if not published_at:
        return None
    try:
        when = datetime.fromisoformat(str(published_at))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - when).days)


def _snippet(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:_SNIPPET_CHARS] + ("…" if len(collapsed) > _SNIPPET_CHARS else "")


def _rerank(
    candidates: list[tuple[float, dict[str, Any]]],
) -> list[tuple[float, dict[str, Any]]]:
    days = [_days_since(row.get("published_at")) for _, row in candidates]
    valid = [d for d in days if d is not None]
    low = min(valid) if valid else 0.0
    span = (max(valid) - low) if valid and max(valid) != low else 1.0
    reranked: list[tuple[float, dict[str, Any]]] = []
    for (similarity, row), day in zip(candidates, days, strict=False):
        recency = 0.0 if day is None else 1.0 - (day - low) / span
        reranked.append((_SIM_WEIGHT * similarity + _RECENCY_WEIGHT * recency, row))
    reranked.sort(key=lambda item: item[0], reverse=True)
    return reranked


def _link_citations(answer: str, citations: list[Citation]) -> str:
    # Replace longer indices first so "[1]" does not corrupt "[10]".
    for citation in sorted(citations, key=lambda c: c.index, reverse=True):
        answer = answer.replace(f"[{citation.index}]", f"[[{citation.index}]]({citation.url})")
    return answer


async def index_channel(
    database: Database,
    embed_provider: LLMProvider,
    channel_id: str,
    *,
    force: bool = False,
    on_progress: ProgressCallback | None = None,
) -> IndexResult:
    """Chunk, embed, and store a channel's transcripts."""
    if database.get_channel(channel_id) is None:
        raise AskError(f"Channel {channel_id} is not in the database")
    videos_indexed = 0
    chunks_indexed = 0
    videos = database.list_videos(channel_id)
    total = len(videos)
    for position, video in enumerate(videos, start=1):
        await report(on_progress, f"Embedding video {position}/{total}", position, total)
        if not force and database.video_is_indexed(video.id):
            continue
        transcript = database.get_transcript(video.id)
        if transcript is None or not transcript.segments:
            continue
        chunks = chunk_transcript(transcript.segments)
        if not chunks:
            continue
        try:
            vectors = await embed_chunks(embed_provider, chunks)
        except LLMError as error:
            raise AskError(f"Embedding failed (is Ollama running?): {error}") from error
        database.delete_chunks_for_video(video.id)
        database.add_chunk_embeddings(
            [
                (video.id, video.channel_id, c.chunk_index, c.start_seconds, c.text, json.dumps(v))
                for c, v in zip(chunks, vectors, strict=False)
            ]
        )
        videos_indexed += 1
        chunks_indexed += len(chunks)
    return IndexResult(
        channel_id=channel_id,
        videos_indexed=videos_indexed,
        chunks_indexed=chunks_indexed,
        total_chunks=database.count_chunk_embeddings([channel_id]),
    )


async def retrieve_chunks(
    database: Database,
    embed_provider: LLMProvider,
    question: str,
    channel_ids: list[str] | None,
    top_k: int = _TOP_K,
    top_n: int = _TOP_N,
) -> list[tuple[float, dict[str, Any]]]:
    """Embed the question and return the top reranked chunk rows."""
    rows = database.list_chunk_embeddings(channel_ids)
    if not rows:
        raise AskError("Nothing is indexed for that selection yet. Index a channel first.")
    try:
        query_vectors = await embed_provider.embed([question])
    except LLMError as error:
        raise AskError(f"Embedding failed (is Ollama running?): {error}") from error
    if not query_vectors:
        raise AskError("The embedding model returned no vector")
    scored = [(_cosine(query_vectors[0], row["embedding"]), row) for row in rows]
    scored.sort(key=lambda item: item[0], reverse=True)
    return _rerank(scored[:top_k])[:top_n]


_SYSTEM = (
    "You answer questions using only the numbered sources provided. Cite every "
    "claim with its source number in brackets, like [1] or [2][3]. If the sources "
    "do not contain the answer, say so plainly. Be concise."
)


async def ask_question(
    database: Database,
    embed_provider: LLMProvider,
    answer_provider: LLMProvider,
    question: str,
    *,
    channel_ids: list[str] | None = None,
    model: str | None = None,
    top_k: int = _TOP_K,
    top_n: int = _TOP_N,
    on_progress: ProgressCallback | None = None,
) -> AskResult:
    """Answer a question against indexed chunks with citations."""
    if not question.strip():
        raise AskError("Ask a question")
    await report(on_progress, "Embedding the question")
    top = await retrieve_chunks(database, embed_provider, question, channel_ids, top_k, top_n)
    await report(on_progress, "Retrieving relevant moments")

    blocks: list[str] = []
    citations: list[Citation] = []
    for index, (_score, row) in enumerate(top, start=1):
        start = float(row["start_seconds"] or 0.0)
        title = str(row["video_title"])
        blocks.append(f'[{index}] "{title}" @ {format_clock(start)}\n{row["text"]}')
        citations.append(
            Citation(
                index=index,
                video_id=str(row["video_id"]),
                title=title,
                start_seconds=start,
                url=watch_url(str(row["video_id"]), start),
                snippet=_snippet(str(row["text"])),
            )
        )

    await report(on_progress, "Answering")
    prompt = f"Question: {question}\n\nSources:\n" + "\n\n".join(blocks)
    try:
        answer = await answer_provider.complete(
            prompt, model=model, system=_SYSTEM, max_tokens=1024, temperature=0.2
        )
    except LLMError as error:
        raise AskError(str(error)) from error
    return AskResult(answer=_link_citations(answer.strip(), citations), citations=citations)
