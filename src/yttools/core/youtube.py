# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Async wrappers around yt-dlp.

Every call shells out to yt-dlp via ``asyncio.create_subprocess_exec`` so the
event loop is never blocked. Metadata calls always pass ``--skip-download`` and
``--no-warnings`` and request JSON output. Unavailable, members-only, and live
videos are surfaced as typed exceptions so callers can log and skip them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yttools.core.models import Chapter, VideoMetadata, VideoStub
from yttools.core.urls import ChannelURL

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120.0
LISTING_TIMEOUT = 600.0
YTDLP_COMMAND: list[str] = [sys.executable, "-m", "yt_dlp"]

# When YouTube serves its anti-bot gate, the request is retried a few times with
# linear backoff. The gate is intermittent, so a retry usually clears it; cookies
# (see ``YouTubeOptions``) clear it reliably.
BOT_CHECK_RETRIES = 2
BOT_CHECK_BACKOFF = 5.0


@dataclass(frozen=True)
class YouTubeOptions:
    """Per-call yt-dlp options for authentication and rate limiting.

    ``cookies_from_browser`` and ``cookies_file`` both clear YouTube's bot gate;
    when both are set the browser source takes precedence. ``sleep_requests`` is
    the delay (seconds) yt-dlp waits between requests.
    """

    cookies_from_browser: str = ""
    cookies_file: str = ""
    sleep_requests: float = 0.0

    def extra_args(self) -> list[str]:
        """Build the yt-dlp flags these options imply."""
        args: list[str] = []
        if self.cookies_from_browser:
            args += ["--cookies-from-browser", self.cookies_from_browser]
        elif self.cookies_file:
            args += ["--cookies", str(Path(self.cookies_file).expanduser())]
        if self.sleep_requests > 0:
            args += ["--sleep-requests", f"{self.sleep_requests:g}"]
        return args


def _extra_args(options: YouTubeOptions | None) -> list[str]:
    return options.extra_args() if options else []


class YouTubeError(RuntimeError):
    """Base class for yt-dlp failures."""


class VideoUnavailableError(YouTubeError):
    """The video is private, deleted, or otherwise inaccessible."""


class MembersOnlyError(YouTubeError):
    """The video requires a channel membership."""


class LiveStreamError(YouTubeError):
    """The video is a live stream in progress or scheduled."""


class BotCheckError(YouTubeError):
    """YouTube served its "sign in to confirm you're not a bot" gate.

    Resolved by supplying cookies via :class:`YouTubeOptions`. Raised only after
    the built-in retries are exhausted.
    """


# Matched case-insensitively against stderr. Kept apostrophe-free so the
# typographic apostrophe in yt-dlp's message does not matter.
_BOT_CHECK_MARKERS = ("sign in to confirm", "not a bot", "confirm you")


def _is_bot_check(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(marker in lowered for marker in _BOT_CHECK_MARKERS)


async def _run_ytdlp_once(
    args: list[str], *, timeout: float = DEFAULT_TIMEOUT
) -> tuple[int, str, str]:
    """Run yt-dlp once and return (returncode, stdout, stderr)."""
    process = await asyncio.create_subprocess_exec(
        *YTDLP_COMMAND,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise YouTubeError("yt-dlp timed out") from None
    return (
        process.returncode or 0,
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
    )


async def _run_ytdlp(
    args: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = BOT_CHECK_RETRIES,
    backoff: float = BOT_CHECK_BACKOFF,
) -> tuple[int, str, str]:
    """Run yt-dlp, retrying with linear backoff when the bot gate is hit."""
    attempt = 0
    while True:
        code, stdout, stderr = await _run_ytdlp_once(args, timeout=timeout)
        if code == 0 or attempt >= retries or not _is_bot_check(stderr):
            return code, stdout, stderr
        attempt += 1
        logger.debug("bot check tripped; retrying yt-dlp (attempt %d of %d)", attempt, retries)
        await asyncio.sleep(backoff * attempt)


def _classify_error(stderr: str) -> YouTubeError:
    lowered = stderr.lower()
    if _is_bot_check(stderr):
        return BotCheckError(stderr.strip())
    if "members-only" in lowered or "join this channel" in lowered:
        return MembersOnlyError(stderr.strip())
    if "private video" in lowered:
        return VideoUnavailableError(stderr.strip())
    if "this live event will begin" in lowered or "premieres in" in lowered:
        return LiveStreamError(stderr.strip())
    unavailable_markers = (
        "video unavailable",
        "has been removed",
        "no longer available",
        "removed by the uploader",
        "account associated with this video has been terminated",
        "this video is not available",
    )
    if any(marker in lowered for marker in unavailable_markers):
        return VideoUnavailableError(stderr.strip())
    return YouTubeError(stderr.strip() or "yt-dlp failed")


def _watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _stub_from_flat(entry: dict[str, Any]) -> VideoStub:
    return VideoStub(
        id=entry["id"],
        title=entry.get("title"),
        duration=entry.get("duration"),
        url=entry.get("url") or entry.get("webpage_url"),
    )


def _published_at(data: dict[str, Any]) -> datetime | None:
    timestamp = data.get("timestamp") or data.get("release_timestamp")
    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(timestamp, tz=UTC)
    upload_date = data.get("upload_date")
    if isinstance(upload_date, str) and len(upload_date) == 8:
        try:
            return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _metadata_from_json(data: dict[str, Any]) -> VideoMetadata:
    chapters = [
        Chapter(start=float(chapter.get("start_time", 0.0)), title=str(chapter.get("title", "")))
        for chapter in (data.get("chapters") or [])
        if isinstance(chapter, dict)
    ]
    uploader_id = data.get("uploader_id")
    handle = uploader_id if isinstance(uploader_id, str) and uploader_id.startswith("@") else None
    duration = data.get("duration")
    live_status = data.get("live_status")
    return VideoMetadata(
        id=data["id"],
        title=data.get("title") or data["id"],
        description=data.get("description"),
        channel_id=data.get("channel_id"),
        channel_title=data.get("channel") or data.get("uploader"),
        channel_handle=handle,
        channel_subscriber_count=data.get("channel_follower_count"),
        published_at=_published_at(data),
        duration_seconds=int(duration) if isinstance(duration, (int, float)) else None,
        view_count=data.get("view_count"),
        like_count=data.get("like_count"),
        thumbnail_url=data.get("thumbnail"),
        chapters=chapters,
        tags=[str(tag) for tag in (data.get("tags") or [])],
        is_live=bool(data.get("is_live")) or live_status in {"is_live", "is_upcoming", "post_live"},
        availability=data.get("availability"),
    )


async def list_channel_videos(
    channel: ChannelURL, *, limit: int | None = None, options: YouTubeOptions | None = None
) -> list[VideoStub]:
    """List a channel's uploads as lightweight stubs via a flat listing."""
    args = ["--flat-playlist", "--dump-json", "--no-warnings", *_extra_args(options)]
    if limit is not None:
        args += ["--playlist-end", str(limit)]
    args.append(channel.listing_target())
    code, stdout, stderr = await _run_ytdlp(args, timeout=LISTING_TIMEOUT)
    if code != 0 and not stdout.strip():
        raise _classify_error(stderr)
    return _parse_stub_lines(stdout)


async def list_playlist_videos(
    playlist_id: str, *, limit: int | None = None, options: YouTubeOptions | None = None
) -> list[VideoStub]:
    """List a playlist's videos as lightweight stubs via a flat listing."""
    args = ["--flat-playlist", "--dump-json", "--no-warnings", *_extra_args(options)]
    if limit is not None:
        args += ["--playlist-end", str(limit)]
    args.append(f"https://www.youtube.com/playlist?list={playlist_id}")
    code, stdout, stderr = await _run_ytdlp(args, timeout=LISTING_TIMEOUT)
    if code != 0 and not stdout.strip():
        raise _classify_error(stderr)
    return _parse_stub_lines(stdout)


def _parse_stub_lines(stdout: str) -> list[VideoStub]:
    stubs: list[VideoStub] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        if entry.get("id"):
            stubs.append(_stub_from_flat(entry))
    return stubs


async def get_video_metadata(
    video_id: str, *, options: YouTubeOptions | None = None
) -> VideoMetadata:
    """Fetch full metadata for a single video.

    Raises:
        VideoUnavailableError, MembersOnlyError, LiveStreamError, BotCheckError,
        or YouTubeError.
    """
    args = [
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        *_extra_args(options),
        _watch_url(video_id),
    ]
    code, stdout, stderr = await _run_ytdlp(args)
    if code != 0:
        raise _classify_error(stderr)
    data = json.loads(stdout)
    metadata = _metadata_from_json(data)
    if metadata.is_live:
        raise LiveStreamError(f"{video_id} is a live or upcoming stream")
    return metadata


async def download_captions(
    video_id: str,
    output_dir: Path,
    *,
    languages: list[str] | None = None,
    options: YouTubeOptions | None = None,
) -> Path | None:
    """Download a VTT caption file, preferring manual captions then auto-captions.

    Returns the path to the downloaded VTT, or ``None`` when the video has no
    captions in any of the requested languages.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    langs = ",".join(languages) if languages else "en"
    output_template = str(output_dir / "%(id)s.%(ext)s")
    args = [
        "--skip-download",
        "--no-warnings",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        langs,
        "--sub-format",
        "vtt",
        *_extra_args(options),
        "-o",
        output_template,
        _watch_url(video_id),
    ]
    code, _stdout, stderr = await _run_ytdlp(args)
    if code != 0:
        raise _classify_error(stderr)
    matches = sorted(output_dir.glob(f"{video_id}*.vtt"))
    if not matches:
        logger.debug("no captions found for a video")
        return None
    return matches[0]
