# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for transcript exporters."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from yttools.core.exports import (
    bundle_zip,
    format_clock,
    render,
    render_json,
    render_markdown,
    render_srt,
    watch_url,
    write_export,
)
from yttools.core.models import Segment, Transcript, Video


def _video() -> Video:
    return Video(id="dQw4w9WgXcQ", title="A Talk", channel_id="UC_test", duration_seconds=12)


def _transcript() -> Transcript:
    return Transcript(
        video_id="dQw4w9WgXcQ",
        language="en",
        is_auto_generated=False,
        text="hello world goodbye",
        segments=[
            Segment(start=0.0, end=4.0, text="hello world"),
            Segment(start=65.0, end=70.0, text="goodbye"),
        ],
        word_count=3,
    )


def test_watch_url_with_and_without_timestamp() -> None:
    assert watch_url("abc") == "https://www.youtube.com/watch?v=abc"
    assert watch_url("abc", 65.4) == "https://www.youtube.com/watch?v=abc&t=65s"


def test_format_clock() -> None:
    assert format_clock(65.0) == "01:05"
    assert format_clock(3725.0) == "1:02:05"
    assert format_clock(3.5, srt=True) == "00:00:03,500"


def test_render_markdown_has_timestamp_links() -> None:
    md = render_markdown(_video(), _transcript())
    assert "# A Talk" in md
    assert "[01:05](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=65s)" in md
    assert "hello world" in md


def test_render_srt_is_sequential() -> None:
    srt = render_srt(_transcript())
    assert srt.startswith("1\n00:00:00,000 --> 00:00:04,000\nhello world")
    assert "2\n00:01:05,000 --> 00:01:10,000\ngoodbye" in srt


def test_render_json_roundtrips() -> None:
    payload = json.loads(render_json(_video(), _transcript()))
    assert payload["video"]["id"] == "dQw4w9WgXcQ"
    assert payload["transcript"]["word_count"] == 3
    assert len(payload["transcript"]["segments"]) == 2


def test_render_txt_contains_prose() -> None:
    txt = render("txt", _video(), _transcript())
    assert "A Talk" in txt
    assert "hello world goodbye" in txt


def test_exports_include_stats_line() -> None:
    video = Video(
        id="dQw4w9WgXcQ",
        title="A Talk",
        duration_seconds=12,
        view_count=12345,
        like_count=678,
        comment_count=9,
    )
    for fmt in ("txt", "md"):
        body = render(fmt, video, _transcript())
        assert "12,345 views" in body
        assert "678 likes" in body
        assert "9 comments" in body


def test_write_export_creates_file(tmp_path: Path) -> None:
    path = write_export(tmp_path, "md", _video(), _transcript())
    assert path.exists()
    assert path.name == "dQw4w9WgXcQ.md"


def test_bundle_zip_contains_all_formats() -> None:
    raw = bundle_zip([(_video(), _transcript())], ["txt", "md", "srt", "json"])
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = set(archive.namelist())
    assert names == {
        "dQw4w9WgXcQ.txt",
        "dQw4w9WgXcQ.md",
        "dQw4w9WgXcQ.srt",
        "dQw4w9WgXcQ.json",
    }
