# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Transcript exporters: plain text, Markdown with timestamp links, SRT, JSON."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Literal

from yttools.core.models import Transcript, Video

ExportFormat = Literal["txt", "md", "srt", "json"]
EXPORT_FORMATS: tuple[ExportFormat, ...] = ("txt", "md", "srt", "json")


def watch_url(video_id: str, start_seconds: float | None = None) -> str:
    """Build a YouTube watch URL, optionally deep-linked to a timestamp."""
    base = f"https://www.youtube.com/watch?v={video_id}"
    if start_seconds is None:
        return base
    return f"{base}&t={int(start_seconds)}s"


def format_clock(seconds: float, *, srt: bool = False) -> str:
    """Format seconds as ``HH:MM:SS`` (or ``HH:MM:SS,mmm`` for SRT)."""
    total = max(0.0, seconds)
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = int(total % 60)
    if srt:
        millis = round((total - int(total)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def render_txt(video: Video, transcript: Transcript) -> str:
    header = f"{video.title}\n{watch_url(video.id)}\n"
    return f"{header}\n{transcript.text}\n"


def render_markdown(video: Video, transcript: Transcript) -> str:
    lines = [f"# {video.title}", "", f"[Watch on YouTube]({watch_url(video.id)})", ""]
    for segment in transcript.segments:
        stamp = format_clock(segment.start)
        link = watch_url(video.id, segment.start)
        lines.append(f"**[{stamp}]({link})** {segment.text}")
        lines.append("")
    if not transcript.segments:
        lines.append(transcript.text)
    return "\n".join(lines).rstrip() + "\n"


def render_srt(transcript: Transcript) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(transcript.segments, start=1):
        start = format_clock(segment.start, srt=True)
        end = format_clock(segment.end, srt=True)
        blocks.append(f"{index}\n{start} --> {end}\n{segment.text}\n")
    return "\n".join(blocks)


def render_json(video: Video, transcript: Transcript) -> str:
    payload = {
        "video": video.model_dump(mode="json"),
        "transcript": {
            "language": transcript.language,
            "is_auto_generated": transcript.is_auto_generated,
            "word_count": transcript.word_count,
            "text": transcript.text,
            "segments": [segment.model_dump() for segment in transcript.segments],
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def render(fmt: ExportFormat, video: Video, transcript: Transcript) -> str:
    if fmt == "txt":
        return render_txt(video, transcript)
    if fmt == "md":
        return render_markdown(video, transcript)
    if fmt == "srt":
        return render_srt(transcript)
    if fmt == "json":
        return render_json(video, transcript)
    raise ValueError(f"Unknown export format: {fmt}")


def write_export(directory: Path, fmt: ExportFormat, video: Video, transcript: Transcript) -> Path:
    """Render and write a single transcript export, returning the file path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{video.id}.{fmt}"
    path.write_text(render(fmt, video, transcript), encoding="utf-8")
    return path


def bundle_zip(items: list[tuple[Video, Transcript]], formats: list[ExportFormat]) -> bytes:
    """Bundle multiple transcripts in the given formats into a zip archive."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for video, transcript in items:
            for fmt in formats:
                archive.writestr(f"{video.id}.{fmt}", render(fmt, video, transcript))
    return buffer.getvalue()
