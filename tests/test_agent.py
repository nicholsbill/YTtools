# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the Ask agent. The model is scripted; tools run against a real DB."""

from __future__ import annotations

import json

from yttools.core.db import Database
from yttools.core.models import Channel, Segment, Transcript, Video
from yttools.tools.agent import _Toolbox, run_agent
from yttools.tools.ask import index_channel


class _ScriptedProvider:
    """Returns canned responses in order (clamping to the last one)."""

    name = "ans"
    default_model = "m"

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls = 0

    async def complete(self, prompt: str, **kwargs: object) -> str:
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


class _FakeEmbed:
    name = "emb"
    default_model = "e"

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        return [[1.0, 0.0, 1.0] for _ in texts]


def _seed(db: Database) -> None:
    db.upsert_channel(Channel(id="UCkat", title="Katina Eats Kilos"))
    db.upsert_channel(Channel(id="UCbeard", title="BeardMeatsFood"))
    rows = [
        ("UCkat", "k1", "a giant steak challenge today", 100),
        ("UCkat", "k2", "another steak challenge attempt", 200),
        ("UCbeard", "b1", "the monster steak challenge", 5000),
    ]
    for channel_id, vid, text, views in rows:
        db.upsert_video(Video(id=vid, channel_id=channel_id, title=text[:18], view_count=views))
        db.upsert_transcript(
            Transcript(
                video_id=vid,
                language="en",
                is_auto_generated=True,
                text=text,
                segments=[Segment(start=0.0, end=5.0, text=text)],
            )
        )


# -- the data tools compute real numbers --------------------------------


def test_toolbox_search_counts_per_channel(db: Database) -> None:
    _seed(db)
    box = _Toolbox(db, _FakeEmbed())
    assert box.search_videos("steak challenge", channel="Katina")["match_count"] == 2
    assert box.search_videos("steak challenge", channel="BeardMeatsFood")["match_count"] == 1


def test_toolbox_channel_stats(db: Database) -> None:
    _seed(db)
    stats = _Toolbox(db, _FakeEmbed()).channel_stats("Katina")
    assert stats["video_count"] == 2
    assert stats["total_views"] == 300


def test_toolbox_unknown_channel_is_an_error(db: Database) -> None:
    _seed(db)
    result = _Toolbox(db, _FakeEmbed()).search_videos("steak", channel="Nope")
    assert "error" in result


# -- the agent loop ------------------------------------------------------


async def test_agent_runs_tools_then_answers(db: Database) -> None:
    _seed(db)
    provider = _ScriptedProvider(
        [
            json.dumps({"tool": "search_videos", "args": {"query": "steak", "channel": "Katina"}}),
            json.dumps(
                {"tool": "search_videos", "args": {"query": "steak", "channel": "BeardMeatsFood"}}
            ),
            json.dumps({"answer": "Katina did 2 and Beard did 1."}),
        ]
    )
    result = await run_agent(db, provider, _FakeEmbed(), "how many steak challenges each?")
    assert "Katina did 2" in result.answer
    assert len(result.steps) == 2
    assert result.steps[0].startswith("search_videos(")


async def test_agent_content_search_adds_citations(db: Database) -> None:
    _seed(db)
    await index_channel(db, _FakeEmbed(), "UCkat")
    provider = _ScriptedProvider(
        [
            json.dumps({"tool": "content_search", "args": {"query": "steak"}}),
            json.dumps({"answer": "They talk about it [1]."}),
        ]
    )
    result = await run_agent(db, provider, _FakeEmbed(), "what did they say about steak?")
    assert result.citations
    assert "](https://www.youtube.com/watch?v=" in result.answer


async def test_agent_answers_without_an_index(db: Database) -> None:
    _seed(db)
    provider = _ScriptedProvider(
        [
            json.dumps({"tool": "channel_stats", "args": {"channel": "Katina"}}),
            json.dumps({"answer": "Katina has 2 videos."}),
        ]
    )
    # No index built; a metadata question still works.
    result = await run_agent(db, provider, _FakeEmbed(), "how many videos?")
    assert result.answer == "Katina has 2 videos."


async def test_agent_forces_an_answer_after_max_steps(db: Database) -> None:
    _seed(db)
    provider = _ScriptedProvider([json.dumps({"tool": "list_channels", "args": {}})])
    result = await run_agent(db, provider, _FakeEmbed(), "q", max_steps=2)
    assert result.answer  # falls back to a graceful message
    assert len(result.steps) == 2
