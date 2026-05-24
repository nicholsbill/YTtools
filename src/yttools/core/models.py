# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Pydantic domain models mirroring the database schema and the data passed
between the YouTube, transcript, and tool layers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from yttools.core.urls import ChannelURL, ParsedURL, PlaylistURL, VideoURL

QuoteType = Literal["statement", "prediction", "stat", "claim", "list"]
SummaryTargetType = Literal["video", "channel", "playlist"]
SummaryType = Literal["overview", "topics", "guests", "cadence"]
JobStatus = Literal["queued", "running", "done", "error", "cancelled"]
VideoFetchState = Literal[
    "queued",
    "fetching_metadata",
    "fetching_transcript",
    "done",
    "skipped",
    "no-captions",
    "error",
]


class Chapter(BaseModel):
    start: float
    title: str


class Segment(BaseModel):
    """A single timed line of a transcript."""

    start: float
    end: float
    text: str


class Channel(BaseModel):
    id: str
    handle: str | None = None
    title: str
    description: str | None = None
    subscriber_count: int | None = None
    video_count: int | None = None
    first_seen_at: datetime | None = None
    last_refreshed_at: datetime | None = None


class Playlist(BaseModel):
    id: str
    channel_id: str | None = None
    title: str
    description: str | None = None
    video_count: int | None = None
    first_seen_at: datetime | None = None
    last_refreshed_at: datetime | None = None


class Video(BaseModel):
    id: str
    channel_id: str | None = None
    title: str
    description: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    thumbnail_url: str | None = None
    chapters: list[Chapter] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    first_seen_at: datetime | None = None
    last_refreshed_at: datetime | None = None


class Transcript(BaseModel):
    video_id: str
    language: str
    is_auto_generated: bool
    text: str
    segments: list[Segment] = Field(default_factory=list)
    word_count: int | None = None
    fetched_at: datetime | None = None


class Quote(BaseModel):
    id: int | None = None
    video_id: str
    text: str
    quote_type: QuoteType
    start_seconds: float | None = None
    end_seconds: float | None = None
    context: str | None = None
    speaker_guess: str | None = None
    model_used: str | None = None
    extracted_at: datetime | None = None


class Summary(BaseModel):
    id: int | None = None
    target_type: SummaryTargetType
    target_id: str
    summary_type: SummaryType
    content: str
    model_used: str | None = None
    generated_at: datetime | None = None


class Topic(BaseModel):
    id: int | None = None
    channel_id: str
    label: str
    first_video_id: str | None = None
    last_video_id: str | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    video_count: int = 0


class Job(BaseModel):
    id: str
    kind: str
    status: JobStatus
    input_json: str | None = None
    output_json: str | None = None
    error_message: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None


class VideoStub(BaseModel):
    """Lightweight result of a flat channel or playlist listing."""

    id: str
    title: str | None = None
    duration: float | None = None
    url: str | None = None


class VideoMetadata(BaseModel):
    """Full per-video metadata extracted from yt-dlp JSON output."""

    id: str
    title: str
    description: str | None = None
    channel_id: str | None = None
    channel_title: str | None = None
    channel_handle: str | None = None
    channel_subscriber_count: int | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    thumbnail_url: str | None = None
    chapters: list[Chapter] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    is_live: bool = False
    availability: str | None = None

    def to_channel(self) -> Channel | None:
        if not self.channel_id:
            return None
        return Channel(
            id=self.channel_id,
            handle=self.channel_handle,
            title=self.channel_title or self.channel_id,
            subscriber_count=self.channel_subscriber_count,
        )

    def to_video(self) -> Video:
        return Video(
            id=self.id,
            channel_id=self.channel_id,
            title=self.title,
            description=self.description,
            published_at=self.published_at,
            duration_seconds=self.duration_seconds,
            view_count=self.view_count,
            like_count=self.like_count,
            comment_count=self.comment_count,
            thumbnail_url=self.thumbnail_url,
            chapters=self.chapters,
            tags=self.tags,
        )


__all__ = [
    "Channel",
    "ChannelURL",
    "Chapter",
    "Job",
    "JobStatus",
    "ParsedURL",
    "Playlist",
    "PlaylistURL",
    "Quote",
    "QuoteType",
    "Segment",
    "Summary",
    "SummaryTargetType",
    "SummaryType",
    "Topic",
    "Transcript",
    "Video",
    "VideoFetchState",
    "VideoMetadata",
    "VideoStub",
    "VideoURL",
]
