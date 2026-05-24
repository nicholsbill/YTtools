# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Compare: see how multiple channels cover overlapping ground.

Reuses the persisted topic extraction (auto-running it for any channel that has
none). Produces topic overlap (shared vs. unique, after fuzzy alignment across
channels), distinctive vocabulary per channel (TF-IDF with each channel's
transcript corpus as one document), and the first/last time each channel touched
a shared topic. No LLM calls beyond the one-time topic extraction.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from pydantic import BaseModel, Field

from yttools.core.db import Database
from yttools.core.llm import LLMProvider
from yttools.core.progress import ProgressCallback, report
from yttools.tools.summarize import ensure_channel_topics

_LABEL_MERGE_RATIO = 0.8
_TOP_TERMS = 30
_MAX_CHANNEL_CHARS = 200_000
_TOKEN = re.compile(r"[a-z]{3,}")
_STOPWORDS = {
    "the",
    "and",
    "that",
    "this",
    "with",
    "for",
    "you",
    "are",
    "but",
    "not",
    "have",
    "was",
    "they",
    "what",
    "all",
    "can",
    "your",
    "from",
    "out",
    "get",
    "has",
    "his",
    "her",
    "him",
    "she",
    "our",
    "their",
    "would",
    "could",
    "should",
    "about",
    "just",
    "like",
    "really",
    "going",
    "know",
    "think",
    "yeah",
    "right",
    "okay",
    "thing",
    "things",
    "lot",
    "way",
    "want",
    "kind",
    "actually",
    "because",
    "there",
    "here",
    "them",
    "then",
    "than",
    "were",
    "been",
    "very",
    "much",
    "more",
    "some",
    "into",
    "over",
    "also",
    "how",
    "why",
    "who",
    "when",
    "where",
    "which",
    "people",
    "one",
    "two",
    "got",
    "did",
    "does",
    "doing",
    "make",
    "made",
    "see",
    "say",
    "said",
}


class CompareError(RuntimeError):
    """Raised when a comparison cannot be produced."""


class ChannelRef(BaseModel):
    id: str
    title: str


class SharedTopic(BaseModel):
    label: str
    channels: list[str] = Field(default_factory=list)


class TermScore(BaseModel):
    term: str
    score: float


class TopicTiming(BaseModel):
    label: str
    channel_id: str
    first: str | None = None
    last: str | None = None
    count: int = 0


class CompareResult(BaseModel):
    channels: list[ChannelRef] = Field(default_factory=list)
    shared_topics: list[SharedTopic] = Field(default_factory=list)
    unique_topics: dict[str, list[str]] = Field(default_factory=dict)
    vocabulary: dict[str, list[TermScore]] = Field(default_factory=dict)
    timing: list[TopicTiming] = Field(default_factory=list)


@dataclass
class _Cluster:
    label: str
    norm: str
    spellings: Counter[str] = field(default_factory=Counter)
    channels: set[str] = field(default_factory=set)
    # channel_id -> set of original labels that fell into this cluster
    per_channel_labels: dict[str, set[str]] = field(default_factory=dict)


def _cluster_across(channel_labels: dict[str, list[str]]) -> list[_Cluster]:
    clusters: list[_Cluster] = []
    for channel_id, labels in channel_labels.items():
        for label in labels:
            normalized = label.lower()
            match = next(
                (
                    c
                    for c in clusters
                    if SequenceMatcher(None, normalized, c.norm).ratio() >= _LABEL_MERGE_RATIO
                ),
                None,
            )
            if match is None:
                match = _Cluster(label=label, norm=normalized)
                clusters.append(match)
            match.spellings[label] += 1
            match.channels.add(channel_id)
            match.per_channel_labels.setdefault(channel_id, set()).add(label)
    for cluster in clusters:
        cluster.label = cluster.spellings.most_common(1)[0][0]
    return clusters


def _vocabulary(channel_text: dict[str, str]) -> dict[str, list[TermScore]]:
    counts: dict[str, Counter[str]] = {}
    doc_freq: Counter[str] = Counter()
    for channel_id, text in channel_text.items():
        tokens = [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]
        channel_counter = Counter(tokens)
        counts[channel_id] = channel_counter
        for term in channel_counter:
            doc_freq[term] += 1
    total_docs = len(channel_text)
    vocabulary: dict[str, list[TermScore]] = {}
    for channel_id, channel_counter in counts.items():
        total = sum(channel_counter.values()) or 1
        scored = [
            TermScore(
                term=term,
                score=round((count / total) * math.log(total_docs / doc_freq[term] + 1.0), 5),
            )
            for term, count in channel_counter.items()
        ]
        scored.sort(key=lambda t: t.score, reverse=True)
        vocabulary[channel_id] = scored[:_TOP_TERMS]
    return vocabulary


def _timing(
    database: Database, clusters: list[_Cluster], shared: list[_Cluster]
) -> list[TopicTiming]:
    timing: list[TopicTiming] = []
    for cluster in shared:
        for channel_id in sorted(cluster.channels):
            member_labels = cluster.per_channel_labels.get(channel_id, set())
            dates = [
                str(row["published_at"])
                for row in database.list_video_topics(channel_id)
                if row["label"] in member_labels and row["published_at"]
            ]
            timing.append(
                TopicTiming(
                    label=cluster.label,
                    channel_id=channel_id,
                    first=min(dates) if dates else None,
                    last=max(dates) if dates else None,
                    count=len(dates),
                )
            )
    return timing


def _channel_text(database: Database, channel_id: str) -> str:
    parts: list[str] = []
    size = 0
    for video in database.list_videos(channel_id):
        transcript = database.get_transcript(video.id)
        if transcript is None:
            continue
        parts.append(transcript.text)
        size += len(transcript.text)
        if size >= _MAX_CHANNEL_CHARS:
            break
    return " ".join(parts)


async def compare_channels(
    database: Database,
    provider: LLMProvider,
    channel_ids: list[str],
    *,
    model: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> CompareResult:
    """Compare 2-5 channels by topic overlap, vocabulary, and topic timing."""
    unique_ids = list(dict.fromkeys(channel_ids))
    if not 2 <= len(unique_ids) <= 5:
        raise CompareError("Pick between 2 and 5 channels to compare")

    refs: list[ChannelRef] = []
    channel_labels: dict[str, list[str]] = {}
    channel_text: dict[str, str] = {}
    for position, channel_id in enumerate(unique_ids, start=1):
        channel = database.get_channel(channel_id)
        if channel is None:
            raise CompareError(f"Channel {channel_id} is not in the database")
        refs.append(ChannelRef(id=channel_id, title=channel.title))
        await report(
            on_progress, f"Preparing topics for {channel.title}", position, len(unique_ids)
        )
        await ensure_channel_topics(
            database, provider, channel_id, model=model, on_progress=on_progress
        )
        channel_labels[channel_id] = [t.label for t in database.list_topics(channel_id)]
        channel_text[channel_id] = _channel_text(database, channel_id)

    await report(on_progress, "Computing overlap and vocabulary")
    clusters = _cluster_across(channel_labels)
    shared = [c for c in clusters if len(c.channels) >= 2]
    unique_topics: dict[str, list[str]] = {cid: [] for cid in unique_ids}
    for cluster in clusters:
        if len(cluster.channels) == 1:
            (only,) = tuple(cluster.channels)
            unique_topics[only].append(cluster.label)

    return CompareResult(
        channels=refs,
        shared_topics=[
            SharedTopic(label=c.label, channels=sorted(c.channels))
            for c in sorted(shared, key=lambda c: len(c.channels), reverse=True)
        ],
        unique_topics=unique_topics,
        vocabulary=_vocabulary(channel_text),
        timing=_timing(database, clusters, shared),
    )
