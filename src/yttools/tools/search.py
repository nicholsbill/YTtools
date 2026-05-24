# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Search: full-text search across stored transcripts.

User queries are turned into FTS5 MATCH expressions (phrase, boolean, and prefix
syntax pass through; plain terms are quoted to stay syntax-safe). Results come
back BM25-ranked from the database. Each hit's snippet is mapped to the nearest
transcript segment so the result links to the exact YouTube timestamp.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime

from pydantic import BaseModel, Field

from yttools.core.db import Database
from yttools.core.exports import watch_url
from yttools.core.models import Segment
from yttools.core.transcripts import segment_at_offset

# Control-character markers the database wraps around matched terms in snippets.
_MATCH_START = "\x02"
_MATCH_END = "\x03"
_ELLIPSIS = " … "
_ADVANCED_QUERY = re.compile(r'["*()]|(?:^|\s)(?:AND|OR|NOT|NEAR)(?:\s|$)')
_TOKEN = re.compile(r"[\w']+", re.UNICODE)

DEFAULT_LIMIT = 50


class SearchError(ValueError):
    """Raised when a query cannot be parsed as an FTS expression."""


class SearchFilters(BaseModel):
    channel_ids: list[str] = Field(default_factory=list)
    published_after: str | None = None
    published_before: str | None = None
    min_duration_minutes: float | None = None
    max_duration_minutes: float | None = None


class SearchResult(BaseModel):
    video_id: str
    title: str
    channel_id: str | None = None
    channel_title: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    score: float
    snippet: str
    start_seconds: float
    url: str


class SearchResponse(BaseModel):
    query: str
    total: int
    results: list[SearchResult] = Field(default_factory=list)


def build_match_query(query: str) -> str:
    """Turn user input into an FTS5 MATCH expression.

    Quotes, prefixes (``*``), parentheses, and the boolean operators pass through
    unchanged. Plain queries are split into tokens and each is quoted, which keeps
    punctuation from triggering FTS syntax errors while preserving implicit AND.
    """
    stripped = query.strip()
    if not stripped:
        raise SearchError("Empty search query")
    if _ADVANCED_QUERY.search(stripped):
        return stripped
    tokens = _TOKEN.findall(stripped)
    if not tokens:
        raise SearchError("No searchable terms in query")
    return " ".join(f'"{token}"' for token in tokens)


def _minutes_to_seconds(minutes: float | None) -> int | None:
    return int(minutes * 60) if minutes is not None else None


def _resolve_start_seconds(
    marked_snippet: str, segments_text: str, segments: list[Segment]
) -> float:
    """Map a marked snippet back to the timestamp of its matching segment."""
    if not segments:
        return 0.0
    fragments = marked_snippet.split(_ELLIPSIS)
    anchor = next((fragment for fragment in fragments if _MATCH_START in fragment), fragments[0])
    marker_index = anchor.find(_MATCH_START)
    clean_anchor = anchor.replace(_MATCH_START, "").replace(_MATCH_END, "")
    base = segments_text.find(clean_anchor)
    if base != -1 and marker_index != -1:
        offset = base + marker_index
    else:
        term = _first_marked_term(marked_snippet)
        located = segments_text.lower().find(term.lower()) if term else -1
        offset = located if located != -1 else 0
    segment = segment_at_offset(segments, max(0, offset))
    return segment.start if segment else 0.0


def _first_marked_term(marked_snippet: str) -> str:
    start = marked_snippet.find(_MATCH_START)
    end = marked_snippet.find(_MATCH_END)
    if start == -1 or end == -1 or end <= start:
        return ""
    return marked_snippet[start + 1 : end]


def _display_snippet(marked_snippet: str) -> str:
    return marked_snippet.replace(_MATCH_START, "**").replace(_MATCH_END, "**")


def search(
    database: Database,
    query: str,
    *,
    filters: SearchFilters | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> SearchResponse:
    """Run a transcript search and return BM25-ranked results with timestamps."""
    filters = filters or SearchFilters()
    match_query = build_match_query(query)
    channel_ids = filters.channel_ids or None
    min_seconds = _minutes_to_seconds(filters.min_duration_minutes)
    max_seconds = _minutes_to_seconds(filters.max_duration_minutes)
    try:
        rows = database.search_fts(
            match_query,
            channel_ids=channel_ids,
            published_after=filters.published_after,
            published_before=filters.published_before,
            min_duration_seconds=min_seconds,
            max_duration_seconds=max_seconds,
            limit=limit,
            offset=offset,
        )
        total = database.count_search_fts(
            match_query,
            channel_ids=channel_ids,
            published_after=filters.published_after,
            published_before=filters.published_before,
            min_duration_seconds=min_seconds,
            max_duration_seconds=max_seconds,
        )
    except sqlite3.OperationalError as error:
        raise SearchError(f"Invalid search query: {error}") from error

    results: list[SearchResult] = []
    for row in rows:
        transcript = database.get_transcript(row["video_id"])
        marked = row["snippet"] or ""
        if transcript is not None:
            start = _resolve_start_seconds(marked, transcript.text, transcript.segments)
        else:
            start = 0.0
        results.append(
            SearchResult(
                video_id=row["video_id"],
                title=row["title"],
                channel_id=row["channel_id"],
                channel_title=row["channel_title"],
                published_at=_parse_dt(row["published_at"]),
                duration_seconds=row["duration_seconds"],
                score=float(row["score"]),
                snippet=_display_snippet(marked),
                start_seconds=start,
                url=watch_url(row["video_id"], start),
            )
        )
    return SearchResponse(query=query, total=total, results=results)


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
