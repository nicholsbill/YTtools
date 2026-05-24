# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the Blog tool. The LLM provider is faked; no network access."""

from __future__ import annotations

import json

import pytest

from yttools.core.db import Database
from yttools.core.models import Segment, Transcript, Video
from yttools.tools.blog import BlogError, generate_blog


class _FakeProvider:
    """Records the prompt/kwargs and returns a canned JSON composition."""

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


def _seed_video(db: Database, *, with_transcript: bool = True) -> None:
    db.upsert_video(Video(id="vid00000001", title="A Talk", duration_seconds=600))
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


_PIECE = json.dumps(
    {
        "title": "A Critic's Take",
        "markdown": "## Verdict\n\nTwo thumbs up. An original review in the critic's voice.",
        "key_moments": [
            {"label": "the big attempt", "start_seconds": 125},
            {"label": "out of range", "start_seconds": 99999},
        ],
    }
)


async def test_generate_uses_model_markdown_verbatim(db: Database) -> None:
    _seed_video(db)
    provider = _FakeProvider(_PIECE)
    result = await generate_blog(
        db, provider, "vid00000001", style="a movie review", length="short"
    )
    assert result.title == "A Critic's Take"
    assert result.markdown.startswith("# A Critic's Take")
    assert "*Based on [A Talk]" in result.markdown
    # The model's own prose is used as-is (no forced per-section transcript dump).
    assert "An original review in the critic's voice." in result.markdown
    assert result.model_used == "fake-1"


async def test_style_and_json_mode_reach_the_model(db: Database) -> None:
    _seed_video(db)
    provider = _FakeProvider(_PIECE)
    await generate_blog(db, provider, "vid00000001", style="a TV newscaster report")
    assert provider.last_kwargs.get("response_format") == "json"
    # The requested style drives the prompt, and the transcript is supplied as source.
    assert "a TV newscaster report" in provider.last_prompt
    assert "[0]" in provider.last_prompt


async def test_key_moments_become_clamped_links(db: Database) -> None:
    _seed_video(db)
    result = await generate_blog(db, _FakeProvider(_PIECE), "vid00000001")
    assert "## Key moments" in result.markdown
    assert "watch?v=vid00000001&t=125s" in result.markdown
    # 99999s is clamped to the 600s duration rather than linking out of range.
    assert "watch?v=vid00000001&t=600s" in result.markdown


async def test_no_key_moments_omits_section(db: Database) -> None:
    _seed_video(db)
    payload = json.dumps({"title": "T", "markdown": "Body.", "key_moments": []})
    result = await generate_blog(db, _FakeProvider(payload), "vid00000001")
    assert "Key moments" not in result.markdown


async def test_title_override_wins(db: Database) -> None:
    _seed_video(db)
    result = await generate_blog(
        db, _FakeProvider(_PIECE), "vid00000001", title_override="My Own Title"
    )
    assert result.title == "My Own Title"
    assert result.markdown.startswith("# My Own Title")


async def test_missing_video_raises(db: Database) -> None:
    with pytest.raises(BlogError):
        await generate_blog(db, _FakeProvider(_PIECE), "nope")


async def test_missing_transcript_raises(db: Database) -> None:
    _seed_video(db, with_transcript=False)
    with pytest.raises(BlogError):
        await generate_blog(db, _FakeProvider(_PIECE), "vid00000001")


async def test_invalid_json_raises(db: Database) -> None:
    _seed_video(db)
    with pytest.raises(BlogError):
        await generate_blog(db, _FakeProvider("not json at all"), "vid00000001")


async def test_fenced_json_is_parsed(db: Database) -> None:
    _seed_video(db)
    fenced = f"```json\n{_PIECE}\n```"
    result = await generate_blog(db, _FakeProvider(fenced), "vid00000001")
    assert "A Critic's Take" in result.markdown
