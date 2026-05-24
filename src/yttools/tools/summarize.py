# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Summarize: structured digests of a channel.

Four summary types: ``overview`` (map-reduce over transcripts), ``topics``
(per-video label extraction, then fuzzy clustering, persisted to the topics
tables for Compare and Timeline to reuse), ``guests`` (per-video interview
extraction), and ``cadence`` (pure SQL over publish dates and durations, no LLM).
Results are cached in the ``summaries`` table and reused unless forced.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher

from pydantic import BaseModel, Field

from yttools.core.db import Database
from yttools.core.llm import LLMError, LLMProvider
from yttools.core.models import Summary, Topic, Video

SUMMARY_TYPES: tuple[str, ...] = ("overview", "topics", "guests", "cadence")

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_MAP_BATCH = 10
_MAX_VIDEOS = 80
_EXCERPT_WORDS = 350
_LABEL_MERGE_RATIO = 0.8
_NAME_MERGE_RATIO = 0.85


class SummarizeError(RuntimeError):
    """Raised when a channel cannot be summarized."""


@dataclass
class _Cluster:
    label: str
    norm: str
    video_ids: set[str] = field(default_factory=set)
    spellings: Counter[str] = field(default_factory=Counter)


@dataclass
class _Guest:
    name: str
    norm: str
    count: int = 0
    background: str = ""


class SummarySection(BaseModel):
    summary_type: str
    content: str


class SummarizeResult(BaseModel):
    channel_id: str
    sections: list[SummarySection] = Field(default_factory=list)


def _load_json(raw: str) -> dict[str, object]:
    cleaned = _FENCE.sub("", raw.strip())
    if not cleaned:
        return {}
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _excerpt(database: Database, video: Video, *, words: int = _EXCERPT_WORDS) -> str:
    transcript = database.get_transcript(video.id)
    if transcript is None:
        return ""
    return " ".join(transcript.text.split()[:words])


async def _complete(
    provider: LLMProvider, prompt: str, *, model: str | None, json_mode: bool
) -> str:
    try:
        return await provider.complete(
            prompt,
            model=model,
            response_format="json" if json_mode else "text",
            max_tokens=2048,
            temperature=0.3,
        )
    except LLMError as error:
        raise SummarizeError(str(error)) from error


# -- cadence (no LLM) ----------------------------------------------------


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _cadence(videos: list[Video]) -> str:
    dates = sorted(_aware(d) for d in (v.published_at for v in videos) if d is not None)
    if not dates:
        return "No publish dates are available for this channel."
    gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
    durations = [v.duration_seconds / 60 for v in videos if v.duration_seconds]
    now = datetime.now(UTC)
    recent = sum(1 for d in dates if (now - d).days <= 90)
    span_days = (dates[-1] - dates[0]).days or 1
    per_month = len(dates) / (span_days / 30.0)
    lines = [
        "## Cadence",
        "",
        f"- Videos with dates: **{len(dates)}** over **{span_days}** days",
        f"- Posting rate: **{per_month:.1f}** videos/month",
    ]
    if gaps:
        lines.append(f"- Median gap between videos: **{statistics.median(gaps):.0f}** days")
        lines.append(f"- Longest hiatus: **{max(gaps)}** days")
    if durations:
        lines.append(f"- Median length: **{statistics.median(durations):.0f}** minutes")
    lines.append(f"- Videos in the last 90 days: **{recent}**")
    return "\n".join(lines) + "\n"


# -- overview (map-reduce) ----------------------------------------------


async def _overview(
    database: Database,
    channel_title: str,
    videos: list[Video],
    provider: LLMProvider,
    model: str | None,
) -> str:
    usable = [v for v in videos if database.transcript_exists(v.id)][:_MAX_VIDEOS]
    if not usable:
        return "No transcripts are available to summarize."
    batch_summaries: list[str] = []
    for start in range(0, len(usable), _MAP_BATCH):
        batch = usable[start : start + _MAP_BATCH]
        blocks = [f"Title: {v.title}\n{_excerpt(database, v)}" for v in batch]
        prompt = (
            "Summarize in 2-3 sentences what these videos collectively cover.\n\n"
            + "\n\n---\n\n".join(blocks)
        )
        batch_summaries.append(await _complete(provider, prompt, model=model, json_mode=False))
    reduce_prompt = (
        f'You are describing the YouTube channel "{channel_title}". Using these '
        "per-batch notes, write a 3-paragraph Markdown overview of what the channel "
        "is about, its focus, and its style. Do not invent facts.\n\n"
        + "\n\n".join(f"- {s.strip()}" for s in batch_summaries)
    )
    body = await _complete(provider, reduce_prompt, model=model, json_mode=False)
    return f"## Overview\n\n{body.strip()}\n"


# -- topics (extract + cluster + persist) -------------------------------


async def _topics(
    database: Database,
    channel_id: str,
    videos: list[Video],
    provider: LLMProvider,
    model: str | None,
) -> str:
    usable = [v for v in videos if database.transcript_exists(v.id)][:_MAX_VIDEOS]
    if not usable:
        return "No transcripts are available to extract topics from."
    # video_id -> list of raw labels
    per_video: list[tuple[Video, list[str]]] = []
    for video in usable:
        prompt = (
            'Return JSON {"topics": ["label", ...]} with 3 to 7 short topic labels '
            f"(1-4 words each) for this video.\n\nTitle: {video.title}\n{_excerpt(database, video)}"
        )
        data = _load_json(await _complete(provider, prompt, model=model, json_mode=True))
        raw_topics = data.get("topics", [])
        labels = (
            [str(t).strip() for t in raw_topics if str(t).strip()]
            if isinstance(raw_topics, list)
            else []
        )
        per_video.append((video, labels))

    clusters = _cluster_labels(per_video)
    database.clear_topics(channel_id)
    ranked = sorted(clusters, key=lambda c: len(c.video_ids), reverse=True)
    for cluster in ranked:
        topic_id = database.add_topic(
            Topic(channel_id=channel_id, label=cluster.label, video_count=len(cluster.video_ids))
        )
        for video_id in cluster.video_ids:
            database.add_video_topic(video_id, topic_id)

    lines = ["## Topics", ""]
    for index, cluster in enumerate(ranked[:25], start=1):
        lines.append(f"{index}. **{cluster.label}** — {len(cluster.video_ids)} video(s)")
    return "\n".join(lines) + "\n"


def _cluster_labels(per_video: list[tuple[Video, list[str]]]) -> list[_Cluster]:
    """Merge similar labels across videos into clusters via fuzzy matching."""
    clusters: list[_Cluster] = []
    for video, labels in per_video:
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
            match.video_ids.add(video.id)
            match.spellings[label] += 1
    for cluster in clusters:
        # Display the most common original spelling.
        cluster.label = cluster.spellings.most_common(1)[0][0]
    return clusters


# -- guests --------------------------------------------------------------


async def _guests(
    database: Database, videos: list[Video], provider: LLMProvider, model: str | None
) -> str:
    usable = [v for v in videos if database.transcript_exists(v.id)][:_MAX_VIDEOS]
    guests: list[_Guest] = []
    for video in usable:
        prompt = (
            'Return JSON {"guests": [{"name": "...", "background": "..."}]} listing any '
            "people interviewed as guests in this video. Return an empty list if it is "
            f"not an interview.\n\nTitle: {video.title}\n{_excerpt(database, video)}"
        )
        data = _load_json(await _complete(provider, prompt, model=model, json_mode=True))
        entries = data.get("guests", [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            if not name:
                continue
            existing = next(
                (
                    g
                    for g in guests
                    if SequenceMatcher(None, g.norm, name.lower()).ratio() >= _NAME_MERGE_RATIO
                ),
                None,
            )
            if existing is None:
                guests.append(
                    _Guest(
                        name=name,
                        norm=name.lower(),
                        count=1,
                        background=str(entry.get("background", "")).strip(),
                    )
                )
            else:
                existing.count += 1
    if not guests:
        return "## Guests\n\nNo interview guests were detected on this channel.\n"
    lines = ["## Guests", ""]
    for guest in sorted(guests, key=lambda g: g.count, reverse=True):
        suffix = f" — {guest.background}" if guest.background else ""
        lines.append(f"- **{guest.name}** ({guest.count} episode(s)){suffix}")
    return "\n".join(lines) + "\n"


async def ensure_channel_topics(
    database: Database,
    provider: LLMProvider,
    channel_id: str,
    *,
    model: str | None = None,
    force: bool = False,
) -> None:
    """Extract and persist topics for a channel if it has none yet.

    Compare and Timeline call this so they never depend on the user having run
    Summarize first.
    """
    if not force and database.list_topics(channel_id):
        return
    videos = database.list_videos(channel_id)
    if videos:
        await _topics(database, channel_id, videos, provider, model)


# -- orchestration -------------------------------------------------------


async def summarize_channel(
    database: Database,
    provider: LLMProvider,
    channel_id: str,
    *,
    summary_types: list[str],
    model: str | None = None,
    force: bool = False,
) -> SummarizeResult:
    """Generate (or reuse) the requested summary types for a channel."""
    channel = database.get_channel(channel_id)
    if channel is None:
        raise SummarizeError(f"Channel {channel_id} is not in the database")
    videos = database.list_videos(channel_id)
    if not videos:
        raise SummarizeError("Channel has no fetched videos")

    model_used = model or getattr(provider, "default_model", None)
    sections: list[SummarySection] = []
    for summary_type in summary_types:
        if summary_type not in SUMMARY_TYPES:
            continue
        if not force:
            cached = database.get_summary("channel", channel_id, summary_type)
            if cached is not None:
                sections.append(SummarySection(summary_type=summary_type, content=cached.content))
                continue
        if summary_type == "cadence":
            content = _cadence(videos)
        elif summary_type == "overview":
            content = await _overview(database, channel.title, videos, provider, model)
        elif summary_type == "topics":
            content = await _topics(database, channel_id, videos, provider, model)
        else:  # guests
            content = await _guests(database, videos, provider, model)
        database.upsert_summary(
            Summary(
                target_type="channel",
                target_id=channel_id,
                summary_type=summary_type,
                content=content,
                model_used=model_used,
            )
        )
        sections.append(SummarySection(summary_type=summary_type, content=content))
    return SummarizeResult(channel_id=channel_id, sections=sections)
