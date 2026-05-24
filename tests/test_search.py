# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the Search tool: ranking, snippets, filters, and timestamps."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from yttools.core.db import Database
from yttools.core.models import Channel, Segment, Transcript, Video
from yttools.tools.search import (
    SearchError,
    SearchFilters,
    build_match_query,
    search,
)


@pytest.fixture
def search_db(db: Database) -> Database:
    db.upsert_channel(Channel(id="UC_a", title="Alpha"))
    db.upsert_channel(Channel(id="UC_b", title="Beta"))

    db.upsert_video(
        Video(
            id="vid_aaaaaaa1",
            channel_id="UC_a",
            title="Intro to ML",
            duration_seconds=600,
            published_at=datetime(2024, 1, 15, tzinfo=UTC),
        )
    )
    db.upsert_transcript(
        Transcript(
            video_id="vid_aaaaaaa1",
            language="en",
            is_auto_generated=True,
            text="welcome to the channel today we discuss machine learning models "
            "and vector databases for search",
            segments=[
                Segment(start=0.0, end=5.0, text="welcome to the channel"),
                Segment(start=60.0, end=65.0, text="today we discuss machine learning models"),
                Segment(start=120.0, end=125.0, text="and vector databases for search"),
            ],
        )
    )

    db.upsert_video(
        Video(
            id="vid_bbbbbbb2",
            channel_id="UC_b",
            title="Deep dive",
            duration_seconds=6000,
            published_at=datetime(2024, 6, 20, tzinfo=UTC),
            view_count=4242,
            like_count=99,
            comment_count=8,
        )
    )
    db.upsert_transcript(
        Transcript(
            video_id="vid_bbbbbbb2",
            language="en",
            is_auto_generated=True,
            text="machine learning machine learning machine learning is the whole topic",
            segments=[
                Segment(
                    start=0.0,
                    end=10.0,
                    text="machine learning machine learning machine learning is the whole topic",
                )
            ],
        )
    )
    return db


def test_build_match_query_quotes_plain_terms() -> None:
    assert build_match_query("machine learning") == '"machine" "learning"'


@pytest.mark.parametrize(
    "advanced",
    ['"machine learning"', "crypto AND NOT regulation", "pyth*", "(a OR b)"],
)
def test_build_match_query_passes_advanced_through(advanced: str) -> None:
    assert build_match_query(advanced) == advanced


def test_build_match_query_rejects_empty() -> None:
    with pytest.raises(SearchError):
        build_match_query("   ")


def test_search_returns_ranked_results(search_db: Database) -> None:
    response = search(search_db, "machine")
    assert response.total == 2
    # The video that says "machine" most often ranks first.
    assert response.results[0].video_id == "vid_bbbbbbb2"
    # Stats ride along with each result.
    assert response.results[0].view_count == 4242
    assert response.results[0].like_count == 99
    assert response.results[0].comment_count == 8


def test_snippet_highlights_match(search_db: Database) -> None:
    response = search(search_db, "vector")
    assert response.results
    assert "**" in response.results[0].snippet


def test_timestamp_maps_within_tolerance(search_db: Database) -> None:
    response = search(search_db, "vector")
    result = next(r for r in response.results if r.video_id == "vid_aaaaaaa1")
    assert abs(result.start_seconds - 120.0) <= 2.0
    assert result.url == "https://www.youtube.com/watch?v=vid_aaaaaaa1&t=120s"


def test_timestamp_for_early_match(search_db: Database) -> None:
    response = search(search_db, "welcome")
    result = response.results[0]
    assert abs(result.start_seconds - 0.0) <= 2.0


def test_phrase_query(search_db: Database) -> None:
    response = search(search_db, '"machine learning"')
    assert response.total == 2


def test_prefix_query(search_db: Database) -> None:
    response = search(search_db, "mach*")
    assert response.total == 2


def test_channel_filter(search_db: Database) -> None:
    response = search(search_db, "machine", filters=SearchFilters(channel_ids=["UC_a"]))
    assert response.total == 1
    assert response.results[0].video_id == "vid_aaaaaaa1"


def test_duration_filter(search_db: Database) -> None:
    response = search(search_db, "machine", filters=SearchFilters(max_duration_minutes=20))
    assert response.total == 1
    assert response.results[0].video_id == "vid_aaaaaaa1"


def test_date_filter(search_db: Database) -> None:
    response = search(search_db, "machine", filters=SearchFilters(published_after="2024-03-01"))
    assert response.total == 1
    assert response.results[0].video_id == "vid_bbbbbbb2"


def test_limit_caps_results(search_db: Database) -> None:
    response = search(search_db, "machine", limit=1)
    assert len(response.results) == 1
    assert response.total == 2  # total reflects all matches, not the page


def test_no_results(search_db: Database) -> None:
    response = search(search_db, "nonexistentterm")
    assert response.total == 0
    assert response.results == []
