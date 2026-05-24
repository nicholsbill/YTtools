# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the yt-dlp wrappers. The subprocess runner is always mocked."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yttools.core import youtube
from yttools.core.urls import ChannelURL


def _install_runner(
    monkeypatch: pytest.MonkeyPatch, code: int, stdout: str, stderr: str = "", *, side_effect=None
) -> dict[str, list[str]]:
    captured: dict[str, list[str]] = {}

    async def fake_runner(args: list[str], *, timeout: float = youtube.DEFAULT_TIMEOUT):
        captured["args"] = args
        if side_effect is not None:
            side_effect(args)
        return code, stdout, stderr

    monkeypatch.setattr(youtube, "_run_ytdlp", fake_runner)
    return captured


async def test_list_channel_videos_parses_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = "\n".join(
        json.dumps({"id": vid, "title": f"Video {vid}", "duration": 60})
        for vid in ("aaaaaaaaaa1", "bbbbbbbbbb2")
    )
    captured = _install_runner(monkeypatch, 0, lines)
    stubs = await youtube.list_channel_videos(ChannelURL(handle="@test"))
    assert [stub.id for stub in stubs] == ["aaaaaaaaaa1", "bbbbbbbbbb2"]
    assert "--flat-playlist" in captured["args"]
    assert "--no-warnings" in captured["args"]


async def test_list_playlist_videos_targets_playlist_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install_runner(monkeypatch, 0, json.dumps({"id": "xxxxxxxxxx1"}))
    stubs = await youtube.list_playlist_videos("PL12345")
    assert stubs[0].id == "xxxxxxxxxx1"
    assert any("list=PL12345" in arg for arg in captured["args"])


async def test_get_video_metadata_builds_model(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "id": "dQw4w9WgXcQ",
        "title": "A Talk",
        "description": "desc",
        "channel_id": "UCsT0YIqwnpJCM-mx7-gSA4Q",
        "channel": "Example Channel",
        "uploader_id": "@example",
        "channel_follower_count": 1000,
        "timestamp": 1_700_000_000,
        "duration": 642.0,
        "view_count": 50,
        "like_count": 5,
        "comment_count": 12,
        "thumbnail": "https://i.ytimg.com/x.jpg",
        "chapters": [{"start_time": 0.0, "title": "Intro"}, {"start_time": 30.0, "title": "Body"}],
        "tags": ["talk", "demo"],
        "live_status": "not_live",
        "availability": "public",
    }
    captured = _install_runner(monkeypatch, 0, json.dumps(payload))
    meta = await youtube.get_video_metadata("dQw4w9WgXcQ")
    assert meta.title == "A Talk"
    assert meta.channel_id == "UCsT0YIqwnpJCM-mx7-gSA4Q"
    assert meta.channel_handle == "@example"
    assert meta.duration_seconds == 642
    assert meta.view_count == 50
    assert meta.like_count == 5
    assert meta.comment_count == 12
    assert len(meta.chapters) == 2
    assert meta.published_at is not None
    assert "--skip-download" in captured["args"]
    assert "--ignore-no-formats-error" in captured["args"]


async def test_get_video_metadata_rejects_live(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"id": "vid00000001", "title": "Live", "live_status": "is_live"}
    _install_runner(monkeypatch, 0, json.dumps(payload))
    with pytest.raises(youtube.LiveStreamError):
        await youtube.get_video_metadata("vid00000001")


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("ERROR: Private video. Sign in", youtube.VideoUnavailableError),
        (
            "ERROR: Join this channel to get access to members-only content",
            youtube.MembersOnlyError,
        ),
        ("ERROR: Video unavailable. This video has been removed", youtube.VideoUnavailableError),
        ("ERROR: This live event will begin in 2 hours", youtube.LiveStreamError),
        ("ERROR: some other failure", youtube.YouTubeError),
    ],
)
async def test_metadata_error_classification(
    monkeypatch: pytest.MonkeyPatch, stderr: str, expected: type[Exception]
) -> None:
    _install_runner(monkeypatch, 1, "", stderr)
    with pytest.raises(expected):
        await youtube.get_video_metadata("vid00000001")


async def test_download_captions_returns_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def write_vtt(_args: list[str]) -> None:
        (tmp_path / "vid00000001.en.vtt").write_text("WEBVTT\n", encoding="utf-8")

    captured = _install_runner(monkeypatch, 0, "", side_effect=write_vtt)
    result = await youtube.download_captions("vid00000001", tmp_path, languages=["en"])
    assert result is not None
    assert result.suffix == ".vtt"
    assert "--ignore-no-formats-error" in captured["args"]


async def test_download_captions_none_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_runner(monkeypatch, 0, "")
    result = await youtube.download_captions("vid00000001", tmp_path)
    assert result is None


def test_youtube_options_browser_wins_over_file() -> None:
    options = youtube.YouTubeOptions(
        cookies_from_browser="firefox", cookies_file="/tmp/cookies.txt", sleep_requests=0
    )
    assert options.extra_args() == ["--cookies-from-browser", "firefox"]


def test_youtube_options_cookies_file_and_sleep_expand() -> None:
    options = youtube.YouTubeOptions(cookies_file="~/cookies.txt", sleep_requests=1.5)
    args = options.extra_args()
    assert "--cookies" in args
    # The leading ~ is expanded so yt-dlp gets an absolute path.
    assert not any(arg.startswith("~") for arg in args)
    assert args[args.index("--sleep-requests") + 1] == "1.5"


def test_youtube_options_empty_is_noop() -> None:
    assert youtube.YouTubeOptions().extra_args() == []


async def test_options_threaded_into_args(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"id": "vid00000001", "title": "x", "live_status": "not_live"}
    captured = _install_runner(monkeypatch, 0, json.dumps(payload))
    options = youtube.YouTubeOptions(cookies_from_browser="chrome", sleep_requests=1.0)
    await youtube.get_video_metadata("vid00000001", options=options)
    args = captured["args"]
    assert "--cookies-from-browser" in args
    assert args[args.index("--cookies-from-browser") + 1] == "chrome"
    assert "--sleep-requests" in args


async def test_bot_check_classified_as_bot_check_error(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = "ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies-from-browser"
    _install_runner(monkeypatch, 1, "", stderr)
    with pytest.raises(youtube.BotCheckError):
        await youtube.get_video_metadata("vid00000001")


async def test_run_ytdlp_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def fake_once(args: list[str], *, timeout: float = youtube.DEFAULT_TIMEOUT):
        calls["n"] += 1
        if calls["n"] == 1:
            return 1, "", "ERROR: Sign in to confirm you're not a bot"
        return 0, "ok", ""

    monkeypatch.setattr(youtube, "_run_ytdlp_once", fake_once)
    code, stdout, _stderr = await youtube._run_ytdlp(["x"], backoff=0)
    assert code == 0
    assert stdout == "ok"
    assert calls["n"] == 2


async def test_run_ytdlp_gives_up_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def fake_once(args: list[str], *, timeout: float = youtube.DEFAULT_TIMEOUT):
        calls["n"] += 1
        return 1, "", "ERROR: Sign in to confirm you're not a bot"

    monkeypatch.setattr(youtube, "_run_ytdlp_once", fake_once)
    code, _stdout, stderr = await youtube._run_ytdlp(["x"], retries=2, backoff=0)
    assert code == 1
    assert calls["n"] == 3  # initial attempt plus two retries
    assert youtube._is_bot_check(stderr)


async def test_run_ytdlp_does_not_retry_other_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def fake_once(args: list[str], *, timeout: float = youtube.DEFAULT_TIMEOUT):
        calls["n"] += 1
        return 1, "", "ERROR: Private video"

    monkeypatch.setattr(youtube, "_run_ytdlp_once", fake_once)
    code, _stdout, _stderr = await youtube._run_ytdlp(["x"], retries=2, backoff=0)
    assert code == 1
    assert calls["n"] == 1  # no retry for non-bot-check failures
