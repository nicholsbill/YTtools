# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Fetch: download transcripts and metadata from public YouTube URLs.

A :class:`FetchJob` expands the input URLs into video stubs, then drains them
through a bounded pool of workers. Each worker upserts channel and video rows,
optionally downloads and parses captions, and publishes a progress event for
every state transition. Re-runs are idempotent: a video is skipped unless it is
missing a transcript, is stale, or a force refresh was requested.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from yttools.config import Settings
from yttools.core import youtube
from yttools.core.db import Database
from yttools.core.models import Playlist, Transcript, VideoFetchState, VideoStub
from yttools.core.progress import ProgressBus, ProgressEvent, get_bus
from yttools.core.transcripts import parse_vtt
from yttools.core.urls import ChannelURL, PlaylistURL, VideoURL, parse
from yttools.core.youtube import (
    LiveStreamError,
    MembersOnlyError,
    VideoUnavailableError,
    YouTubeError,
)

logger = logging.getLogger(__name__)

STALE_AFTER_DAYS = 7


def youtube_options_from_settings(settings: Settings) -> youtube.YouTubeOptions:
    """Build :class:`youtube.YouTubeOptions` from the ``[youtube]`` config section."""
    return youtube.YouTubeOptions(
        cookies_from_browser=settings.youtube.cookies_from_browser,
        cookies_file=settings.youtube.cookies_file,
        sleep_requests=settings.youtube.sleep_requests,
    )


class FetchConfig(BaseModel):
    """Options controlling a fetch run."""

    include_transcripts: bool = True
    languages: list[str] = Field(default_factory=lambda: ["en"])
    force_refresh: bool = False
    concurrent_videos: int = 2


class FetchResultRow(BaseModel):
    video_id: str
    title: str | None = None
    state: VideoFetchState
    message: str = ""


class FetchSummary(BaseModel):
    job_id: str
    total: int = 0
    done: int = 0
    skipped: int = 0
    no_captions: int = 0
    errors: int = 0
    cancelled: bool = False
    rows: list[FetchResultRow] = Field(default_factory=list)


@dataclass
class _QueueItem:
    stub: VideoStub
    playlist_id: str | None = None
    position: int = 0


@dataclass
class _Counters:
    done: int = 0
    skipped: int = 0
    no_captions: int = 0
    errors: int = 0
    rows: list[FetchResultRow] = field(default_factory=list)


class FetchJob:
    """Coordinates expansion, the worker pool, and progress reporting."""

    def __init__(
        self,
        database: Database,
        urls: list[str],
        config: FetchConfig | None = None,
        *,
        job_id: str | None = None,
        bus: ProgressBus | None = None,
        captions_dir: Path | None = None,
        youtube_options: youtube.YouTubeOptions | None = None,
    ) -> None:
        self.db = database
        self.urls = urls
        self.config = config or FetchConfig()
        self.job_id = job_id or uuid.uuid4().hex
        self.bus = bus or get_bus()
        self.captions_dir = captions_dir or (Path.home() / ".yttools" / "captions")
        self.youtube_options = youtube_options
        self._cancelled = asyncio.Event()
        self._counters = _Counters()
        self._completed = 0
        self._total = 0

    def cancel(self) -> None:
        """Stop scheduling new work. In-flight videos are allowed to finish."""
        self._cancelled.set()

    async def run(self) -> FetchSummary:
        items = await self._expand_all()
        self._total = len(items)
        queue: asyncio.Queue[_QueueItem] = asyncio.Queue()
        for item in items:
            queue.put_nowait(item)

        worker_count = max(1, self.config.concurrent_videos)
        workers = [asyncio.create_task(self._worker(queue)) for _ in range(worker_count)]
        await asyncio.gather(*workers)

        summary = FetchSummary(
            job_id=self.job_id,
            total=self._total,
            done=self._counters.done,
            skipped=self._counters.skipped,
            no_captions=self._counters.no_captions,
            errors=self._counters.errors,
            cancelled=self._cancelled.is_set(),
            rows=self._counters.rows,
        )
        terminal = "job_cancelled" if summary.cancelled else "job_done"
        await self._publish(
            terminal,
            message="Fetch cancelled" if summary.cancelled else "Fetch complete",
            data=summary.model_dump(),
        )
        return summary

    async def _expand_all(self) -> list[_QueueItem]:
        items: list[_QueueItem] = []
        for url in self.urls:
            try:
                items.extend(await self._expand(url))
            except YouTubeError as error:
                logger.debug("could not expand an input URL")
                await self._publish(
                    "video_update",
                    data={"state": "error", "message": f"Could not read source: {error}"},
                )
        return items

    async def _expand(self, url: str) -> list[_QueueItem]:
        parsed = parse(url)
        if isinstance(parsed, VideoURL):
            return [_QueueItem(stub=VideoStub(id=parsed.video_id))]
        if isinstance(parsed, ChannelURL):
            stubs = await youtube.list_channel_videos(parsed, options=self.youtube_options)
            return [_QueueItem(stub=stub) for stub in stubs]
        if isinstance(parsed, PlaylistURL):
            return await self._expand_playlist(parsed)
        return []

    async def _expand_playlist(self, parsed: PlaylistURL) -> list[_QueueItem]:
        stubs = await youtube.list_playlist_videos(parsed.playlist_id, options=self.youtube_options)
        await asyncio.to_thread(
            self.db.upsert_playlist,
            Playlist(id=parsed.playlist_id, title=parsed.playlist_id, video_count=len(stubs)),
        )
        return [
            _QueueItem(stub=stub, playlist_id=parsed.playlist_id, position=index)
            for index, stub in enumerate(stubs)
        ]

    async def _worker(self, queue: asyncio.Queue[_QueueItem]) -> None:
        while not self._cancelled.is_set():
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await self._process(item)
            finally:
                queue.task_done()

    async def _process(self, item: _QueueItem) -> None:
        video_id = item.stub.id
        title = item.stub.title
        await self._emit_state(video_id, "queued", title=title)

        needs_fetch = await asyncio.to_thread(
            self.db.video_needs_fetch,
            video_id,
            force_refresh=self.config.force_refresh,
            max_age_days=STALE_AFTER_DAYS,
        )
        if not needs_fetch:
            await self._record(video_id, "skipped", title=title, message="Already fetched")
            return

        try:
            await self._fetch_one(item)
        except (VideoUnavailableError, MembersOnlyError, LiveStreamError) as error:
            await self._record(video_id, "skipped", title=title, message=str(error))
        except YouTubeError as error:
            await self._record(video_id, "error", title=title, message=str(error))

    async def _fetch_one(self, item: _QueueItem) -> None:
        video_id = item.stub.id
        await self._emit_state(video_id, "fetching_metadata", title=item.stub.title)
        metadata = await youtube.get_video_metadata(video_id, options=self.youtube_options)

        channel = metadata.to_channel()
        if channel is not None:
            await asyncio.to_thread(self.db.upsert_channel, channel)
        await asyncio.to_thread(self.db.upsert_video, metadata.to_video())
        if item.playlist_id is not None:
            await asyncio.to_thread(
                self.db.add_playlist_video, item.playlist_id, video_id, item.position
            )

        if not self.config.include_transcripts:
            await self._record(video_id, "done", title=metadata.title, message="Metadata only")
            return

        await self._emit_state(video_id, "fetching_transcript", title=metadata.title)
        vtt_path = await youtube.download_captions(
            video_id,
            self.captions_dir,
            languages=self.config.languages,
            options=self.youtube_options,
        )
        if vtt_path is None:
            await self._record(
                video_id, "no-captions", title=metadata.title, message="No captions available"
            )
            return

        parsed = await asyncio.to_thread(parse_vtt, vtt_path)
        # yt-dlp's output does not reliably distinguish manual from auto captions,
        # so this defaults to auto-generated (the common case for most channels).
        transcript = Transcript(
            video_id=video_id,
            language=self.config.languages[0] if self.config.languages else "en",
            is_auto_generated=True,
            text=parsed.text,
            segments=parsed.segments,
            word_count=parsed.word_count,
        )
        await asyncio.to_thread(self.db.upsert_transcript, transcript)
        vtt_path.unlink(missing_ok=True)
        await self._record(video_id, "done", title=metadata.title, message="Fetched transcript")

    async def _record(
        self, video_id: str, state: VideoFetchState, *, title: str | None, message: str
    ) -> None:
        if state == "done":
            self._counters.done += 1
        elif state == "skipped":
            self._counters.skipped += 1
        elif state == "no-captions":
            self._counters.no_captions += 1
        elif state == "error":
            self._counters.errors += 1
        self._counters.rows.append(
            FetchResultRow(video_id=video_id, title=title, state=state, message=message)
        )
        self._completed += 1
        await self._emit_state(video_id, state, title=title, message=message)

    async def _emit_state(
        self, video_id: str, state: VideoFetchState, *, title: str | None, message: str = ""
    ) -> None:
        await self._publish(
            "video_update",
            message=message,
            data={"video_id": video_id, "title": title, "state": state, "message": message},
        )

    async def _publish(
        self, event: str, *, message: str = "", data: dict[str, object] | None = None
    ) -> None:
        await self.bus.publish(
            ProgressEvent(
                job_id=self.job_id,
                event=event,
                message=message,
                current=self._completed,
                total=self._total,
                data=data or {},
            )
        )
