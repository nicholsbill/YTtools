# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the SQLite access layer and migration runner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from yttools.core.db import Database
from yttools.core.models import (
    Channel,
    Chapter,
    Job,
    Segment,
    Transcript,
    Video,
)


def _make_video(video_id: str = "vid00000001", channel_id: str = "UC_test") -> Video:
    return Video(
        id=video_id,
        channel_id=channel_id,
        title="A talk about search",
        duration_seconds=600,
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        chapters=[Chapter(start=0.0, title="Intro")],
        tags=["search", "demo"],
    )


def test_migrations_create_core_tables(db: Database) -> None:
    rows = db._fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    names = {row["name"] for row in rows}
    for table in ("channels", "videos", "transcripts", "transcripts_fts", "jobs", "quotes"):
        assert table in names


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "x.db")
    assert database.migrate() == []  # already applied on open
    database.close()


def test_upsert_and_get_channel(db: Database) -> None:
    db.upsert_channel(Channel(id="UC_test", handle="@test", title="Test Channel"))
    fetched = db.get_channel("UC_test")
    assert fetched is not None
    assert fetched.title == "Test Channel"
    assert fetched.last_refreshed_at is not None


def test_upsert_video_roundtrips_chapters_and_tags(db: Database) -> None:
    db.upsert_channel(Channel(id="UC_test", title="Test Channel"))
    db.upsert_video(_make_video())
    fetched = db.get_video("vid00000001")
    assert fetched is not None
    assert fetched.chapters[0].title == "Intro"
    assert fetched.tags == ["search", "demo"]
    assert fetched.duration_seconds == 600


def test_upsert_video_updates_existing(db: Database) -> None:
    db.upsert_channel(Channel(id="UC_test", title="Test Channel"))
    db.upsert_video(_make_video())
    updated = _make_video()
    updated.title = "Renamed"
    db.upsert_video(updated)
    assert db.count_videos() == 1
    fetched = db.get_video("vid00000001")
    assert fetched is not None
    assert fetched.title == "Renamed"


def test_transcript_upsert_indexes_fts(db: Database) -> None:
    db.upsert_channel(Channel(id="UC_test", title="Test Channel"))
    db.upsert_video(_make_video())
    db.upsert_transcript(
        Transcript(
            video_id="vid00000001",
            language="en",
            is_auto_generated=True,
            text="we talk about machine learning and vector search today",
            segments=[Segment(start=0.0, end=5.0, text="machine learning")],
            word_count=9,
        )
    )
    results = db.search_fts("machine")
    assert len(results) == 1
    assert results[0]["video_id"] == "vid00000001"
    assert "\x02" in results[0]["snippet"]


def test_transcript_reindex_on_update(db: Database) -> None:
    db.upsert_channel(Channel(id="UC_test", title="Test Channel"))
    db.upsert_video(_make_video())
    transcript = Transcript(
        video_id="vid00000001",
        language="en",
        is_auto_generated=True,
        text="original content about gardening",
    )
    db.upsert_transcript(transcript)
    transcript.text = "replaced content about astronomy"
    db.upsert_transcript(transcript)
    assert db.search_fts("gardening") == []
    assert len(db.search_fts("astronomy")) == 1


def test_video_needs_fetch_logic(db: Database) -> None:
    db.upsert_channel(Channel(id="UC_test", title="Test Channel"))
    assert db.video_needs_fetch("missing", force_refresh=False) is True

    db.upsert_video(_make_video())
    # Video exists but no transcript yet -> needs fetch.
    assert db.video_needs_fetch("vid00000001", force_refresh=False) is True

    db.upsert_transcript(
        Transcript(video_id="vid00000001", language="en", is_auto_generated=False, text="hi")
    )
    # Fresh transcript -> skip.
    assert db.video_needs_fetch("vid00000001", force_refresh=False) is False
    # force_refresh overrides.
    assert db.video_needs_fetch("vid00000001", force_refresh=True) is True


def test_video_needs_fetch_respects_age(db: Database) -> None:
    db.upsert_channel(Channel(id="UC_test", title="Test Channel"))
    db.upsert_video(_make_video())
    db.upsert_transcript(
        Transcript(video_id="vid00000001", language="en", is_auto_generated=False, text="hi")
    )
    stale = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    db._conn.execute("UPDATE videos SET last_refreshed_at = ? WHERE id = ?", (stale, "vid00000001"))
    db._conn.commit()
    assert db.video_needs_fetch("vid00000001", force_refresh=False) is True


def test_search_filters_by_channel_and_duration(db: Database) -> None:
    db.upsert_channel(Channel(id="UC_a", title="A"))
    db.upsert_channel(Channel(id="UC_b", title="B"))
    for vid, channel, dur in (("vid_aaaaaaa1", "UC_a", 100), ("vid_bbbbbbb1", "UC_b", 5000)):
        db.upsert_video(
            Video(
                id=vid,
                channel_id=channel,
                title="t",
                duration_seconds=dur,
                published_at=datetime(2024, 6, 1, tzinfo=UTC),
            )
        )
        db.upsert_transcript(
            Transcript(
                video_id=vid,
                language="en",
                is_auto_generated=False,
                text="shared keyword about climate policy",
            )
        )
    assert len(db.search_fts("climate")) == 2
    assert len(db.search_fts("climate", channel_ids=["UC_a"])) == 1
    assert len(db.search_fts("climate", max_duration_seconds=200)) == 1
    assert db.count_search_fts("climate") == 2


def test_job_lifecycle(db: Database) -> None:
    db.create_job(Job(id="job-1", kind="fetch", status="queued", progress_total=3))
    assert db.count_active_jobs() == 1
    db.update_job("job-1", status="running", mark_started=True, progress_current=1)
    job = db.get_job("job-1")
    assert job is not None
    assert job.status == "running"
    assert job.started_at is not None
    db.update_job("job-1", status="done", mark_finished=True, progress_current=3)
    assert db.count_active_jobs() == 0
