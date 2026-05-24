# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the Blog tool. The LLM provider is faked; no network access."""

from __future__ import annotations

import json

import pytest

from yttools.core.db import Database
from yttools.core.models import Chapter, Segment, Transcript, Video
from yttools.tools.blog import BlogError, generate_blog


class _FakeProvider:
    """Records the prompt it was given and returns a canned JSON article."""

    name = "fake"
    default_model = "fake-1"

    def __init__(self, payload: str) -> None:
        self._payload = payload
        self.last_prompt = ""
        self.last_kwargs: dict[str, object] = {}

    async def complete(self, prompt: str, **kwargs: object) -> str:
        self.last_prompt = prompt
        self.last_kwargs = kwargs
        return self._payload


def _seed_video(db: Database, *, with_transcript: bool = True, chapters: bool = False) -> None:
    db.upsert_video(
        Video(
            id="vid00000001",
            title="A Talk",
            duration_seconds=600,
            chapters=[Chapter(start=0.0, title="Start"), Chapter(start=120.0, title="Middle")]
            if chapters
            else [],
        )
    )
    if with_transcript:
        db.upsert_transcript(
            Transcript(
                video_id="vid00000001",
                language="en",
                is_auto_generated=True,
                text="intro words then more material later in the talk",
                segments=[
                    Segment(start=0.0, end=10.0, text="intro words"),
                    Segment(start=125.0, end=135.0, text="more material later in the talk"),
                ],
            )
        )


_ARTICLE = json.dumps(
    {
        "title": "Generated Title",
        "sections": [
            {"heading": "Opening", "start_seconds": 0, "markdown": "First part."},
            {"heading": "Later", "start_seconds": 125, "markdown": "Second part."},
        ],
    }
)


async def test_generate_blog_builds_markdown(db: Database) -> None:
    _seed_video(db)
    provider = _FakeProvider(_ARTICLE)
    result = await generate_blog(db, provider, "vid00000001", length="short")
    assert result.title == "Generated Title"
    assert result.markdown.startswith("# Generated Title")
    assert "## Opening" in result.markdown
    assert "## Later" in result.markdown
    # Each section header is followed by a working timestamp deep-link.
    assert "watch?v=vid00000001&t=0s" in result.markdown
    assert "watch?v=vid00000001&t=125s" in result.markdown
    assert result.word_count > 0
    assert result.model_used == "fake-1"


async def test_generate_blog_json_mode_and_timestamps_in_prompt(db: Database) -> None:
    _seed_video(db)
    provider = _FakeProvider(_ARTICLE)
    await generate_blog(db, provider, "vid00000001")
    assert provider.last_kwargs.get("response_format") == "json"
    # The transcript handed to the model carries [seconds] anchors.
    assert "[0]" in provider.last_prompt


async def test_title_override_wins(db: Database) -> None:
    _seed_video(db)
    result = await generate_blog(
        db, _FakeProvider(_ARTICLE), "vid00000001", title_override="My Own Title"
    )
    assert result.title == "My Own Title"
    assert result.markdown.startswith("# My Own Title")


async def test_start_seconds_clamped_to_duration(db: Database) -> None:
    _seed_video(db)
    payload = json.dumps(
        {"title": "T", "sections": [{"heading": "H", "start_seconds": 99999, "markdown": "x"}]}
    )
    result = await generate_blog(db, _FakeProvider(payload), "vid00000001")
    # Duration is 600s, so the link is clamped rather than left out of range.
    assert "watch?v=vid00000001&t=600s" in result.markdown


async def test_missing_video_raises(db: Database) -> None:
    with pytest.raises(BlogError):
        await generate_blog(db, _FakeProvider(_ARTICLE), "nope")


async def test_missing_transcript_raises(db: Database) -> None:
    _seed_video(db, with_transcript=False)
    with pytest.raises(BlogError):
        await generate_blog(db, _FakeProvider(_ARTICLE), "vid00000001")


async def test_invalid_json_raises(db: Database) -> None:
    _seed_video(db)
    with pytest.raises(BlogError):
        await generate_blog(db, _FakeProvider("not json at all"), "vid00000001")


async def test_fenced_json_is_parsed(db: Database) -> None:
    _seed_video(db)
    fenced = f"```json\n{_ARTICLE}\n```"
    result = await generate_blog(db, _FakeProvider(fenced), "vid00000001")
    assert "## Opening" in result.markdown
