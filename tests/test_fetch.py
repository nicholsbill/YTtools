# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""End-to-end tests for the Fetch tool against recorded yt-dlp output.

The subprocess runner is mocked so the full youtube -> fetch -> transcripts ->
database path runs with no network access.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from yttools.core import youtube
from yttools.core.db import Database
from yttools.core.progress import ProgressBus, ProgressEvent
from yttools.tools.fetch import FetchConfig, FetchJob


def _video_id_from_args(args: list[str]) -> str:
    for arg in args:
        if "watch?v=" in arg:
            return parse_qs(urlsplit(arg).query)["v"][0]
    return "unknown"


def _make_runner(fixtures_dir: Path, *, with_captions: bool = True) -> Callable[..., object]:
    listing = (fixtures_dir / "channel_listing.jsonl").read_text(encoding="utf-8")
    meta_template = json.loads((fixtures_dir / "video_metadata.json").read_text(encoding="utf-8"))
    vtt = (fixtures_dir / "sample.vtt").read_text(encoding="utf-8")

    async def runner(args: list[str], *, timeout: float = youtube.DEFAULT_TIMEOUT):
        if "--flat-playlist" in args:
            return 0, listing, ""
        if "--dump-single-json" in args:
            meta = dict(meta_template)
            meta["id"] = _video_id_from_args(args)
            return 0, json.dumps(meta), ""
        if "--write-subs" in args or "--write-auto-subs" in args:
            if with_captions:
                template = args[args.index("-o") + 1]
                out_dir = Path(template).parent
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"{_video_id_from_args(args)}.en.vtt").write_text(vtt, encoding="utf-8")
            return 0, "", ""
        return 1, "", "unexpected invocation"

    return runner


@pytest.fixture
def patched_youtube(monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path) -> Callable[..., object]:
    runner = _make_runner(fixtures_dir)
    monkeypatch.setattr(youtube, "_run_ytdlp", runner)
    return runner


async def test_fetch_channel_end_to_end(
    db: Database, tmp_path: Path, patched_youtube: object
) -> None:
    job = FetchJob(
        db,
        ["https://www.youtube.com/@sample"],
        FetchConfig(),
        captions_dir=tmp_path / "captions",
    )
    summary = await job.run()
    assert summary.total == 3
    assert summary.done == 3
    assert db.count_videos() == 3
    transcript = db.get_transcript("aaaaaaaaaa1")
    assert transcript is not None
    assert "machine learning" in transcript.text
    # The channel row was created from video metadata.
    assert db.get_channel("UCsT0YIqwnpJCM-mx7-gSA4Q") is not None


async def test_fetch_single_video(db: Database, tmp_path: Path, patched_youtube: object) -> None:
    job = FetchJob(db, ["dQw4w9WgXcQ"], FetchConfig(), captions_dir=tmp_path / "c")
    summary = await job.run()
    assert summary.total == 1
    assert summary.done == 1
    assert db.get_video("dQw4w9WgXcQ") is not None


async def test_rerun_is_idempotent(db: Database, tmp_path: Path, patched_youtube: object) -> None:
    urls = ["https://www.youtube.com/@sample"]
    first = await FetchJob(db, urls, FetchConfig(), captions_dir=tmp_path / "c").run()
    assert first.done == 3
    second = await FetchJob(db, urls, FetchConfig(), captions_dir=tmp_path / "c").run()
    assert second.done == 0
    assert second.skipped == 3


async def test_force_refresh_refetches(
    db: Database, tmp_path: Path, patched_youtube: object
) -> None:
    urls = ["https://www.youtube.com/@sample"]
    await FetchJob(db, urls, FetchConfig(), captions_dir=tmp_path / "c").run()
    refreshed = await FetchJob(
        db, urls, FetchConfig(force_refresh=True), captions_dir=tmp_path / "c"
    ).run()
    assert refreshed.done == 3
    assert refreshed.skipped == 0


async def test_metadata_only_scope_skips_transcripts(
    db: Database, tmp_path: Path, patched_youtube: object
) -> None:
    job = FetchJob(
        db,
        ["https://www.youtube.com/@sample"],
        FetchConfig(include_transcripts=False),
        captions_dir=tmp_path / "c",
    )
    summary = await job.run()
    assert summary.done == 3
    assert db.count_videos() == 3
    assert db.get_transcript("aaaaaaaaaa1") is None


async def test_no_captions_still_saves_video(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
) -> None:
    monkeypatch.setattr(youtube, "_run_ytdlp", _make_runner(fixtures_dir, with_captions=False))
    job = FetchJob(
        db, ["https://www.youtube.com/@sample"], FetchConfig(), captions_dir=tmp_path / "c"
    )
    summary = await job.run()
    assert summary.no_captions == 3
    assert summary.done == 0
    assert db.count_videos() == 3
    assert db.get_transcript("aaaaaaaaaa1") is None


@pytest.mark.parametrize(
    "error",
    [
        youtube.VideoUnavailableError("private"),
        youtube.MembersOnlyError("members"),
        youtube.LiveStreamError("live"),
    ],
)
async def test_unavailable_videos_are_skipped(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    async def raise_error(video_id: str, *, options: object = None):
        raise error

    monkeypatch.setattr(youtube, "get_video_metadata", raise_error)
    job = FetchJob(db, ["dQw4w9WgXcQ"], FetchConfig(), captions_dir=tmp_path / "c")
    summary = await job.run()
    assert summary.skipped == 1
    assert summary.done == 0


async def test_progress_events_published(
    db: Database, tmp_path: Path, patched_youtube: object
) -> None:
    bus = ProgressBus()
    job = FetchJob(
        db,
        ["dQw4w9WgXcQ"],
        FetchConfig(),
        bus=bus,
        captions_dir=tmp_path / "c",
    )
    queue = await bus.subscribe(job.job_id)
    summary = await job.run()
    events: list[ProgressEvent] = []
    while not queue.empty():
        item = await queue.get()
        if item is not None:
            events.append(item)
    states = [event.data.get("state") for event in events if event.event == "video_update"]
    assert "queued" in states
    assert "done" in states
    assert summary.done == 1


async def test_fetch_playlist_links_videos(
    db: Database, tmp_path: Path, patched_youtube: object
) -> None:
    job = FetchJob(
        db,
        ["https://www.youtube.com/playlist?list=PLtest12345"],
        FetchConfig(),
        captions_dir=tmp_path / "c",
    )
    summary = await job.run()
    assert summary.done == 3
    assert any(p.id == "PLtest12345" for p in db.list_playlists())
    links = db._fetchall(
        "SELECT video_id FROM playlist_videos WHERE playlist_id = ?", ("PLtest12345",)
    )
    assert len(links) == 3


async def test_mixed_input_channel_and_video(
    db: Database, tmp_path: Path, patched_youtube: object
) -> None:
    job = FetchJob(
        db,
        ["https://www.youtube.com/@sample", "dQw4w9WgXcQ"],
        FetchConfig(),
        captions_dir=tmp_path / "c",
    )
    summary = await job.run()
    assert summary.total == 4
    assert summary.done == 4
    assert db.count_videos() == 4


async def test_youtube_options_passed_to_ytdlp(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    async def runner(args: list[str], *, timeout: float = youtube.DEFAULT_TIMEOUT):
        seen.append(args)
        return 0, json.dumps({"id": "dQw4w9WgXcQ", "title": "x", "live_status": "not_live"}), ""

    monkeypatch.setattr(youtube, "_run_ytdlp", runner)
    options = youtube.YouTubeOptions(cookies_from_browser="firefox", sleep_requests=1.0)
    job = FetchJob(
        db,
        ["dQw4w9WgXcQ"],
        FetchConfig(include_transcripts=False),
        captions_dir=tmp_path / "c",
        youtube_options=options,
    )
    await job.run()
    assert any("--cookies-from-browser" in args for args in seen)


async def test_cancel_before_run_processes_nothing(
    db: Database, tmp_path: Path, patched_youtube: object
) -> None:
    job = FetchJob(
        db, ["https://www.youtube.com/@sample"], FetchConfig(), captions_dir=tmp_path / "c"
    )
    job.cancel()
    summary = await job.run()
    assert summary.cancelled is True
    assert summary.done == 0
