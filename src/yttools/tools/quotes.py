# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Quotes: extract quotable lines from transcripts.

Each transcript is split into timestamped windows and passed to the LLM with a
structured (JSON) prompt that returns declarative statements, statistics,
predictions, claims, and lists, each anchored to an approximate start time.
Results are de-duplicated per video and persisted to the ``quotes`` table.
"""

from __future__ import annotations

import csv
import io
import json
import re
from difflib import SequenceMatcher
from typing import cast, get_args

from pydantic import BaseModel, Field, ValidationError

from yttools.core.db import Database
from yttools.core.exports import watch_url
from yttools.core.llm import LLMError, LLMProvider
from yttools.core.models import Quote, QuoteType, Transcript
from yttools.core.progress import ProgressCallback, report

_QUOTE_TYPES: tuple[str, ...] = get_args(QuoteType)
_DEFAULT_TYPE: QuoteType = "statement"
_MAX_WINDOW_WORDS = 1500
_DEDUPE_RATIO = 0.85
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class QuotesError(RuntimeError):
    """Raised when extraction cannot proceed."""


class _RawQuote(BaseModel):
    text: str
    type: str = _DEFAULT_TYPE
    speaker_guess: str | None = None
    context: str | None = None
    start_seconds: float | None = None


class _RawQuotes(BaseModel):
    quotes: list[_RawQuote] = Field(default_factory=list)


class QuoteOut(BaseModel):
    video_id: str
    video_title: str
    text: str
    quote_type: str
    start_seconds: float | None = None
    url: str
    speaker_guess: str | None = None
    context: str | None = None


class QuotesResult(BaseModel):
    total: int = 0
    quotes: list[QuoteOut] = Field(default_factory=list)


def _timestamped_windows(
    transcript: Transcript, *, max_words: int = _MAX_WINDOW_WORDS
) -> list[str]:
    """Split a transcript into windows of ``[seconds] text`` lines."""
    if not transcript.segments:
        return [transcript.text] if transcript.text.strip() else []
    windows: list[str] = []
    lines: list[str] = []
    words = 0
    for segment in transcript.segments:
        text = segment.text.strip()
        if not text:
            continue
        lines.append(f"[{int(segment.start)}] {text}")
        words += len(text.split())
        if words >= max_words:
            windows.append("\n".join(lines))
            lines, words = [], 0
    if lines:
        windows.append("\n".join(lines))
    return windows


_PROMPT = (
    'Return a JSON object {{"quotes": [...]}} of genuinely quotable lines from the '
    "transcript excerpt below. Each quote object has the fields: "
    '"text" (verbatim), "type", "speaker_guess", "context" (a short surrounding '
    'phrase), and "start_seconds" (the [seconds] marker nearest where it is said).\n'
    "type must be one of: statement, prediction, stat, claim, list.\n"
    "Only include declarative statements, specific statistics, named predictions, "
    "strong claims, or notable lists. Skip filler, transitions, and throat-clearing. "
    "If nothing qualifies, return an empty list.\n\n"
    "Transcript excerpt (each line starts with its time in seconds):\n{window}"
)


def _coerce_type(raw: str) -> QuoteType:
    candidate = (raw or "").strip().lower()
    return cast(QuoteType, candidate) if candidate in _QUOTE_TYPES else _DEFAULT_TYPE


def _parse_window(raw: str) -> list[_RawQuote]:
    cleaned = _FENCE.sub("", raw.strip())
    if not cleaned:
        return []
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    # Tolerate a bare array as well as the requested object.
    if isinstance(data, list):
        data = {"quotes": data}
    try:
        return _RawQuotes.model_validate(data).quotes
    except ValidationError:
        return []


def _dedupe(quotes: list[Quote]) -> list[Quote]:
    kept: list[Quote] = []
    for quote in quotes:
        normalized = " ".join(quote.text.lower().split())
        if any(
            SequenceMatcher(None, normalized, " ".join(k.text.lower().split())).ratio()
            >= _DEDUPE_RATIO
            for k in kept
        ):
            continue
        kept.append(quote)
    return kept


async def extract_quotes(
    database: Database,
    provider: LLMProvider,
    *,
    video_ids: list[str],
    quote_types: list[str] | None = None,
    model: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> QuotesResult:
    """Extract and persist quotes for the given videos, replacing prior quotes."""
    if not video_ids:
        raise QuotesError("No videos to extract quotes from")
    wanted = {t for t in (quote_types or _QUOTE_TYPES) if t in _QUOTE_TYPES}
    model_used = model or getattr(provider, "default_model", None)
    out: list[QuoteOut] = []
    total = len(video_ids)

    for position, video_id in enumerate(video_ids, start=1):
        await report(on_progress, f"Extracting quotes ({position}/{total})", position, total)
        video = database.get_video(video_id)
        transcript = database.get_transcript(video_id)
        if video is None or transcript is None or not transcript.text.strip():
            continue
        duration = float(video.duration_seconds) if video.duration_seconds else None

        collected: list[Quote] = []
        for window in _timestamped_windows(transcript):
            try:
                raw = await provider.complete(
                    _PROMPT.format(window=window),
                    model=model,
                    response_format="json",
                    max_tokens=2048,
                    temperature=0.2,
                )
            except LLMError as error:
                raise QuotesError(str(error)) from error
            for item in _parse_window(raw):
                if not item.text.strip():
                    continue
                start = item.start_seconds
                if start is not None:
                    start = max(0.0, start)
                    if duration is not None:
                        start = min(start, duration)
                collected.append(
                    Quote(
                        video_id=video_id,
                        text=item.text.strip(),
                        quote_type=_coerce_type(item.type),
                        start_seconds=start,
                        context=item.context,
                        speaker_guess=item.speaker_guess,
                        model_used=model_used,
                    )
                )

        deduped = [q for q in _dedupe(collected) if q.quote_type in wanted]
        database.delete_quotes_for_video(video_id)
        database.add_quotes(deduped)
        for quote in deduped:
            out.append(
                QuoteOut(
                    video_id=video_id,
                    video_title=video.title,
                    text=quote.text,
                    quote_type=quote.quote_type,
                    start_seconds=quote.start_seconds,
                    url=watch_url(video_id, quote.start_seconds),
                    speaker_guess=quote.speaker_guess,
                    context=quote.context,
                )
            )

    return QuotesResult(total=len(out), quotes=out)


def load_quotes(
    database: Database, video_ids: list[str], quote_types: list[str] | None
) -> QuotesResult:
    """Load already-extracted quotes without calling the LLM."""
    rows = database.list_quotes(video_ids=video_ids or None, quote_types=quote_types or None)
    titles: dict[str, str] = {}
    for video_id in {row.video_id for row in rows}:
        video = database.get_video(video_id)
        titles[video_id] = video.title if video else video_id
    quotes = [
        QuoteOut(
            video_id=row.video_id,
            video_title=titles.get(row.video_id, row.video_id),
            text=row.text,
            quote_type=row.quote_type,
            start_seconds=row.start_seconds,
            url=watch_url(row.video_id, row.start_seconds),
            speaker_guess=row.speaker_guess,
            context=row.context,
        )
        for row in rows
    ]
    return QuotesResult(total=len(quotes), quotes=quotes)


def quotes_to_csv(result: QuotesResult) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["quote", "type", "video_title", "start_seconds", "url", "speaker", "context"])
    for q in result.quotes:
        writer.writerow(
            [
                q.text,
                q.quote_type,
                q.video_title,
                q.start_seconds,
                q.url,
                q.speaker_guess,
                q.context,
            ]
        )
    return buffer.getvalue()


def quotes_to_markdown(result: QuotesResult) -> str:
    lines = ["# Quotes", ""]
    for q in result.quotes:
        lines.append(f"> {q.text}")
        attribution = f"— *{q.quote_type}*, [{q.video_title}]({q.url})"
        if q.speaker_guess:
            attribution += f" · {q.speaker_guess}"
        lines.append(attribution)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def quotes_to_json(result: QuotesResult) -> str:
    return json.dumps([q.model_dump() for q in result.quotes], indent=2)


def export_quotes(result: QuotesResult, fmt: str) -> tuple[str, str]:
    """Return (body, media_type) for the requested export format."""
    if fmt == "csv":
        return quotes_to_csv(result), "text/csv"
    if fmt == "json":
        return quotes_to_json(result), "application/json"
    if fmt == "md":
        return quotes_to_markdown(result), "text/markdown"
    raise QuotesError(f"Unknown export format: {fmt}")
