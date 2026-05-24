# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Timeline: see when topics rose and fell across a channel.

Two modes. ``auto`` aggregates the persisted topic tables (extracting them first
if missing) and buckets videos per topic per month. ``specific`` tracks
free-text topics by matching them against transcripts; it uses a keyword/token
overlap match (a semantic vector match arrives with the Ask tool's index).
Output is shaped for a stacked-area chart plus per-topic timing stats.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime

from pydantic import BaseModel, Field

from yttools.core.db import Database
from yttools.core.llm import LLMProvider
from yttools.core.progress import ProgressCallback, report
from yttools.tools.summarize import ensure_channel_topics

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_TOP_TOPICS = 20
_MATCH_THRESHOLD = 0.5


class TimelineError(RuntimeError):
    """Raised when a timeline cannot be built."""


class TimelineSeries(BaseModel):
    topic: str
    counts: list[int] = Field(default_factory=list)


class TopicStat(BaseModel):
    topic: str
    total: int = 0
    first_month: str | None = None
    last_month: str | None = None
    peak_month: str | None = None


class TimelineResult(BaseModel):
    mode: str
    months: list[str] = Field(default_factory=list)
    series: list[TimelineSeries] = Field(default_factory=list)
    stats: list[TopicStat] = Field(default_factory=list)


def _month(value: object) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m")
    except ValueError:
        text = str(value)
        return text[:7] if len(text) >= 7 else None


def _assemble(topic_months: dict[str, list[str]]) -> TimelineResult:
    """Turn topic -> list-of-months into aligned series and stats."""
    all_months = sorted({m for months in topic_months.values() for m in months})
    index = {month: i for i, month in enumerate(all_months)}
    series: list[TimelineSeries] = []
    stats: list[TopicStat] = []
    for topic, months in topic_months.items():
        counts = [0] * len(all_months)
        for month in months:
            counts[index[month]] += 1
        series.append(TimelineSeries(topic=topic, counts=counts))
        if months:
            per_month = Counter(months)
            peak = per_month.most_common(1)[0][0]
            stats.append(
                TopicStat(
                    topic=topic,
                    total=len(months),
                    first_month=min(months),
                    last_month=max(months),
                    peak_month=peak,
                )
            )
        else:
            stats.append(TopicStat(topic=topic))
    stats.sort(key=lambda s: s.total, reverse=True)
    return TimelineResult(mode="", months=all_months, series=series, stats=stats)


async def _auto(
    database: Database,
    provider: LLMProvider,
    channel_id: str,
    model: str | None,
    on_progress: ProgressCallback | None = None,
) -> TimelineResult:
    await report(on_progress, "Extracting topics")
    await ensure_channel_topics(
        database, provider, channel_id, model=model, on_progress=on_progress
    )
    rows = database.list_video_topics(channel_id)
    totals: Counter[str] = Counter(row["label"] for row in rows)
    top_labels = {label for label, _ in totals.most_common(_TOP_TOPICS)}
    topic_months: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row["label"] not in top_labels:
            continue
        month = _month(row["published_at"])
        if month:
            topic_months[row["label"]].append(month)
    result = _assemble(topic_months)
    result.mode = "auto"
    return result


def _specific(database: Database, channel_id: str, topics: list[str]) -> TimelineResult:
    wanted = [t.strip() for t in topics if t.strip()][:10]
    if not wanted:
        raise TimelineError("Provide at least one topic to track")
    videos = database.list_videos(channel_id)
    topic_months: dict[str, list[str]] = {topic: [] for topic in wanted}
    for video in videos:
        transcript = database.get_transcript(video.id)
        if transcript is None:
            continue
        text = transcript.text.lower()
        tokens = set(_TOKEN.findall(text))
        month = _month(video.published_at.isoformat() if video.published_at else None)
        if month is None:
            continue
        for topic in wanted:
            if _matches(topic, text, tokens):
                topic_months[topic].append(month)
    result = _assemble(topic_months)
    result.mode = "specific"
    return result


def _matches(topic: str, text: str, tokens: set[str]) -> bool:
    phrase = topic.lower().strip()
    if phrase in text:
        return True
    topic_tokens = _TOKEN.findall(phrase)
    if not topic_tokens:
        return False
    overlap = sum(1 for t in topic_tokens if t in tokens) / len(topic_tokens)
    return overlap >= _MATCH_THRESHOLD


async def build_timeline(
    database: Database,
    provider: LLMProvider,
    channel_id: str,
    *,
    mode: str = "auto",
    topics: list[str] | None = None,
    model: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> TimelineResult:
    """Build a topic-over-time view for a channel."""
    if database.get_channel(channel_id) is None:
        raise TimelineError(f"Channel {channel_id} is not in the database")
    if mode == "specific":
        await report(on_progress, "Scanning transcripts")
        return _specific(database, channel_id, topics or [])
    return await _auto(database, provider, channel_id, model, on_progress)
