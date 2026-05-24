# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Blog: write an original piece from a video, in a style you choose.

The transcript is treated as *source material*, not text to reformat. The model
composes something new — a review, a news report, a personal essay, whatever the
``style`` asks for — in that voice, about the video. Optional timestamp links are
collected into a "Key moments" footer rather than forcing a copied section per
timestamp (which is what made earlier output read like the transcript).
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from yttools.core.db import Database
from yttools.core.exports import format_clock, watch_url
from yttools.core.llm import LLMError, LLMProvider
from yttools.core.models import Transcript, Video
from yttools.core.progress import ProgressCallback, report

BlogLength = Literal["short", "medium", "long"]

# Approximate target word counts the prompt asks the model to aim for.
_LENGTH_WORDS: dict[BlogLength, int] = {"short": 700, "medium": 1300, "long": 2200}
_DEFAULT_STYLE = "an engaging blog post written in your own clear, original voice"
# Cap the transcript fed to the model so a very long video does not blow the
# context window; this is plenty for a typical talk.
_MAX_TRANSCRIPT_CHARS = 40_000
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

_SYSTEM = (
    "You are a versatile, skilled writer. You compose an original piece in the "
    "format and voice the user requests, about the subject they provide. You are a "
    "writer reacting to or transforming the material — you are not the people in "
    "the source video, and you never paste or lightly reword the source text. Write "
    "fluent, original prose."
)


class BlogError(RuntimeError):
    """Raised when a piece cannot be generated."""


class BlogResult(BaseModel):
    video_id: str
    title: str
    markdown: str
    model_used: str | None = None
    word_count: int = 0


class _Moment(BaseModel):
    label: str = ""
    start_seconds: float = 0.0


class _Composition(BaseModel):
    title: str = ""
    markdown: str = ""
    key_moments: list[_Moment] = Field(default_factory=list)


def _timestamped_transcript(transcript: Transcript, *, bucket_seconds: float = 15.0) -> str:
    """Render the transcript as ``[seconds] text`` lines bucketed by time."""
    if not transcript.segments:
        return transcript.text[:_MAX_TRANSCRIPT_CHARS]
    lines: list[str] = []
    bucket_start = transcript.segments[0].start
    buffer: list[str] = []
    for segment in transcript.segments:
        if segment.start - bucket_start >= bucket_seconds and buffer:
            lines.append(f"[{int(bucket_start)}] {' '.join(buffer)}")
            buffer = []
            bucket_start = segment.start
        buffer.append(segment.text.strip())
    if buffer:
        lines.append(f"[{int(bucket_start)}] {' '.join(buffer)}")
    return "\n".join(lines)[:_MAX_TRANSCRIPT_CHARS]


def _build_prompt(video: Video, transcript: Transcript, *, style: str, length: BlogLength) -> str:
    target_words = _LENGTH_WORDS[length]
    return (
        f"Write about {target_words} words: {style}.\n\n"
        "This piece is ABOUT the video described below. Use the transcript only as "
        "source material — summarize, analyze, react to, and reinterpret it in the "
        "requested format and voice. Do NOT reproduce transcript lines, and do NOT "
        "write in the voice of the people in the video, unless the requested style "
        "explicitly asks you to BE one of them. You may quote a short line with "
        "quotation marks if it genuinely strengthens the piece. Use real facts from "
        "the transcript; do not invent events that did not happen.\n\n"
        f"Video title: {video.title}\n"
        f"Duration (seconds): {video.duration_seconds or 'unknown'}\n\n"
        'Return ONLY a JSON object: {"title": "...", "markdown": "the full piece in '
        'Markdown", "key_moments": [{"label": "short note", "start_seconds": 0}]}. '
        "key_moments is optional — pull a few notable moments from the [seconds] "
        "markers, or leave it empty.\n\n"
        "Transcript (each line is prefixed with its start time in seconds):\n"
        f"{_timestamped_transcript(transcript)}"
    )


def _parse_composition(raw: str) -> _Composition:
    cleaned = _FENCE.sub("", raw.strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise BlogError(f"Model did not return valid JSON: {error}") from error
    try:
        return _Composition.model_validate(data)
    except ValueError as error:
        raise BlogError(f"Model JSON did not match the expected shape: {error}") from error


def _render(video: Video, comp: _Composition, *, title_override: str | None) -> str:
    title = (title_override or comp.title or video.title).strip()
    parts = [
        f"# {title}",
        "",
        f"*Based on [{video.title}]({watch_url(video.id)})*",
        "",
        comp.markdown.strip(),
    ]
    if comp.key_moments:
        duration = float(video.duration_seconds) if video.duration_seconds else None
        parts += ["", "## Key moments", ""]
        for moment in comp.key_moments:
            start = max(0.0, moment.start_seconds)
            if duration is not None:
                start = min(start, duration)
            link = f"[{format_clock(start)}]({watch_url(video.id, start)})"
            label = moment.label.strip()
            parts.append(f"- {link}" + (f" — {label}" if label else ""))
    return "\n".join(parts).strip() + "\n"


async def generate_blog(
    database: Database,
    provider: LLMProvider,
    video_id: str,
    *,
    style: str | None = None,
    length: BlogLength = "medium",
    title_override: str | None = None,
    model: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> BlogResult:
    """Write an original piece about a stored video in the requested style."""
    style = (style or "").strip() or _DEFAULT_STYLE
    await report(on_progress, "Reading transcript", 0, 2)
    video = database.get_video(video_id)
    if video is None:
        raise BlogError(f"Video {video_id} is not in the database; fetch it first")
    transcript = database.get_transcript(video_id)
    if transcript is None or not transcript.text.strip():
        raise BlogError(f"Video {video_id} has no transcript to write from")

    prompt = _build_prompt(video, transcript, style=style, length=length)
    await report(on_progress, "Writing", 1, 2)
    try:
        raw = await provider.complete(
            prompt,
            model=model,
            system=_SYSTEM,
            response_format="json",
            max_tokens=4096,
            temperature=0.7,
        )
    except LLMError as error:
        raise BlogError(str(error)) from error

    comp = _parse_composition(raw)
    if not comp.markdown.strip():
        raise BlogError("Model returned no text")
    await report(on_progress, "Done", 2, 2)
    markdown = _render(video, comp, title_override=title_override)
    return BlogResult(
        video_id=video_id,
        title=(title_override or comp.title or video.title).strip(),
        markdown=markdown,
        model_used=model or getattr(provider, "default_model", None),
        word_count=len(markdown.split()),
    )
