# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Blog: turn a single video transcript into a Markdown article.

A stored video's transcript is sent to the configured LLM in one structured
(JSON) pass. The model returns a title and an ordered list of sections, each
anchored to an approximate start time in the transcript. The article is then
assembled with a timestamp deep-link under every section header so each part of
the prose links back to the moment it covers.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from yttools.core.db import Database
from yttools.core.exports import watch_url
from yttools.core.llm import LLMError, LLMProvider
from yttools.core.models import Transcript, Video

BlogLength = Literal["short", "medium", "long"]

# Approximate target word counts the prompt asks the model to aim for.
_LENGTH_WORDS: dict[BlogLength, int] = {"short": 800, "medium": 1500, "long": 2400}
_DEFAULT_TONE = "Match the speaker's own voice and tone."
# Cap the transcript fed to the model so a very long video does not blow the
# context window; this is plenty for a typical talk.
_MAX_TRANSCRIPT_CHARS = 40_000
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class BlogError(RuntimeError):
    """Raised when an article cannot be generated."""


class BlogResult(BaseModel):
    video_id: str
    title: str
    markdown: str
    model_used: str | None = None
    word_count: int = 0


class _Section(BaseModel):
    heading: str
    start_seconds: float = 0.0
    markdown: str = ""


class _Article(BaseModel):
    title: str = ""
    sections: list[_Section] = Field(default_factory=list)


def _timestamped_transcript(transcript: Transcript, *, bucket_seconds: float = 15.0) -> str:
    """Render the transcript as ``[seconds] text`` lines bucketed by time.

    Bucketing keeps the prompt compact while still giving the model timestamp
    anchors it can attach sections to.
    """
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
    rendered = "\n".join(lines)
    return rendered[:_MAX_TRANSCRIPT_CHARS]


def _build_prompt(video: Video, transcript: Transcript, *, length: BlogLength) -> str:
    target_words = _LENGTH_WORDS[length]
    chapter_note = ""
    if video.chapters:
        chapters = "\n".join(f"- [{int(c.start)}] {c.title}" for c in video.chapters)
        chapter_note = (
            "\nThe video has chapters; use them as the section outline and set each "
            "section's start_seconds to the matching chapter start:\n" + chapters + "\n"
        )
    return (
        f"Convert this video transcript into a publishable Markdown article of about "
        f"{target_words} words.\n"
        f"Video title: {video.title}\n"
        f"Duration (seconds): {video.duration_seconds or 'unknown'}\n"
        f"{chapter_note}\n"
        "Write 3 to 7 sections. For each section pick a start_seconds value taken from "
        "the [seconds] markers in the transcript where that section's material begins. "
        "Preserve any notable quotes verbatim. Do not invent facts not in the transcript.\n\n"
        "Return only a JSON object of the form:\n"
        '{"title": "...", "sections": [{"heading": "...", "start_seconds": 0, '
        '"markdown": "section body in Markdown"}]}\n\n'
        "Transcript (each line is prefixed with its start time in seconds):\n"
        f"{_timestamped_transcript(transcript)}"
    )


def _parse_article(raw: str) -> _Article:
    cleaned = _FENCE.sub("", raw.strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise BlogError(f"Model did not return valid JSON: {error}") from error
    try:
        return _Article.model_validate(data)
    except ValueError as error:
        raise BlogError(f"Model JSON did not match the expected shape: {error}") from error


def _render_markdown(video: Video, article: _Article, *, title_override: str | None) -> str:
    title = (title_override or article.title or video.title).strip()
    duration = float(video.duration_seconds) if video.duration_seconds else None
    parts = [f"# {title}", "", f"*Source: [{video.title}]({watch_url(video.id)})*", ""]
    for section in article.sections:
        start = max(0.0, section.start_seconds)
        if duration is not None:
            start = min(start, duration)
        parts.append(f"## {section.heading.strip()}")
        parts.append(f"[Watch this section]({watch_url(video.id, start)})")
        parts.append("")
        parts.append(section.markdown.strip())
        parts.append("")
    return "\n".join(parts).strip() + "\n"


async def generate_blog(
    database: Database,
    provider: LLMProvider,
    video_id: str,
    *,
    tone: str | None = None,
    length: BlogLength = "medium",
    title_override: str | None = None,
    model: str | None = None,
) -> BlogResult:
    """Generate a Markdown article from a stored video's transcript."""
    tone = tone or _DEFAULT_TONE
    video = database.get_video(video_id)
    if video is None:
        raise BlogError(f"Video {video_id} is not in the database; fetch it first")
    transcript = database.get_transcript(video_id)
    if transcript is None or not transcript.text.strip():
        raise BlogError(f"Video {video_id} has no transcript to convert")

    system = (
        "You are an expert editor who turns spoken-video transcripts into clear, "
        "publishable Markdown articles. " + tone
    )
    prompt = _build_prompt(video, transcript, length=length)
    try:
        raw = await provider.complete(
            prompt,
            model=model,
            system=system,
            response_format="json",
            max_tokens=4096,
            temperature=0.4,
        )
    except LLMError as error:
        raise BlogError(str(error)) from error

    article = _parse_article(raw)
    if not article.sections:
        raise BlogError("Model returned no article sections")
    markdown = _render_markdown(video, article, title_override=title_override)
    return BlogResult(
        video_id=video_id,
        title=(title_override or article.title or video.title).strip(),
        markdown=markdown,
        model_used=model or getattr(provider, "default_model", None),
        word_count=len(markdown.split()),
    )
