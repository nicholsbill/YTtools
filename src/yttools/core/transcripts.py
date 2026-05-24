# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Parse WebVTT caption files into clean prose and timed segments.

Auto-generated YouTube captions roll: each cue repeats the tail of the previous
cue and appends a few new words, and they carry inline timing tags. The parser
strips the tags, removes speaker labels and bracketed sound cues, and collapses
the rolling repetition so each stored segment holds only new text. The joined
segment text is what gets indexed, which keeps character offsets aligned with
segment timestamps for the search tool's jump-links.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from yttools.core.models import Segment

_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3}\s*-->\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3}"
)
_CUE_TIMES_RE = re.compile(r"((?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3})")
_INLINE_TAG_RE = re.compile(r"<[^>]+>")
_BRACKETED_RE = re.compile(r"\[[^\]]*\]")
_SPEAKER_RE = re.compile(r"^\s*(>>+\s*)?([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,2}):\s+")
_LEADING_CHEVRON_RE = re.compile(r"^\s*>>+\s*")
_WHITESPACE_RE = re.compile(r"\s+")


class ParsedTranscript(BaseModel):
    """The result of parsing a VTT file."""

    text: str = ""
    segments: list[Segment] = Field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def timestamp_to_seconds(timestamp: str) -> float:
    """Convert an ``HH:MM:SS.mmm`` (or ``MM:SS.mmm``) timestamp to seconds."""
    normalized = timestamp.strip().replace(",", ".")
    parts = [float(part) for part in normalized.split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, minutes, seconds = 0.0, parts[0], parts[1]
    else:
        return parts[0]
    return hours * 3600 + minutes * 60 + seconds


def _clean_line(raw: str) -> str:
    text = _INLINE_TAG_RE.sub("", raw)
    text = _BRACKETED_RE.sub("", text)
    text = _LEADING_CHEVRON_RE.sub("", text)
    text = _SPEAKER_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def clean_text(raw: str) -> str:
    """Strip speaker labels and tags, normalize whitespace, dedupe adjacent lines."""
    cleaned_lines: list[str] = []
    previous = ""
    for line in raw.splitlines():
        cleaned = _clean_line(line)
        if not cleaned or cleaned == previous:
            continue
        cleaned_lines.append(cleaned)
        previous = cleaned
    return " ".join(cleaned_lines)


def _iter_cues(content: str) -> list[tuple[float, float, str]]:
    blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n"))
    cues: list[tuple[float, float, str]] = []
    for block in blocks:
        lines = block.strip("\n").splitlines()
        time_line_index = next(
            (index for index, line in enumerate(lines) if _TIMESTAMP_RE.search(line)),
            None,
        )
        if time_line_index is None:
            continue
        times = _CUE_TIMES_RE.findall(lines[time_line_index])
        if len(times) < 2:
            continue
        start = timestamp_to_seconds(times[0])
        end = timestamp_to_seconds(times[1])
        body = " ".join(lines[time_line_index + 1 :])
        cleaned = _clean_line(body)
        if cleaned:
            cues.append((start, end, cleaned))
    return cues


def _overlap_length(tail: list[str], head: list[str]) -> int:
    """Length of the longest suffix of ``tail`` that prefixes ``head``."""
    for length in range(min(len(tail), len(head)), 0, -1):
        if tail[-length:] == head[:length]:
            return length
    return 0


def parse_vtt(path: Path) -> ParsedTranscript:
    """Parse a VTT file into deduplicated, timestamped segments and joined text.

    Rolling captions overlap word-for-word between consecutive cues, so each cue
    contributes only the words not already present at the tail of the running
    transcript. Cues that add nothing new are dropped.
    """
    content = path.read_text(encoding="utf-8", errors="replace")
    cues = _iter_cues(content)

    segments: list[Segment] = []
    running_words: list[str] = []
    for start, end, text in cues:
        words = text.split()
        if not words:
            continue
        overlap = _overlap_length(running_words, words)
        new_words = words[overlap:]
        if not new_words:
            continue
        running_words.extend(new_words)
        segments.append(Segment(start=start, end=end, text=" ".join(new_words)))

    joined = " ".join(segment.text for segment in segments)
    return ParsedTranscript(text=joined, segments=segments)


def segment_at_offset(segments: list[Segment], offset: int) -> Segment | None:
    """Return the segment containing a character offset in the joined text.

    The joined text is ``" ".join(segment.text ...)`` so each segment occupies a
    contiguous range followed by a single joining space.
    """
    if not segments:
        return None
    cursor = 0
    for segment in segments:
        length = len(segment.text)
        if cursor <= offset <= cursor + length:
            return segment
        cursor += length + 1  # account for the joining space
    return segments[-1]
