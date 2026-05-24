# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Parse and normalize YouTube URLs into a small discriminated union.

A single :func:`parse` entry point accepts the URL forms YouTube uses for
channels, playlists, and videos, plus bare identifiers, and returns one of
:class:`ChannelURL`, :class:`PlaylistURL`, or :class:`VideoURL`.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_PLAYLIST_ID_RE = re.compile(r"^(?:PL|UU|LL|FL|OL|RD|PU)[A-Za-z0-9_-]{10,}$")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


class URLParseError(ValueError):
    """Raised when an input string cannot be recognized as a YouTube URL."""


class ChannelURL(BaseModel):
    kind: Literal["channel"] = "channel"
    handle: str | None = None
    channel_id: str | None = None

    def listing_target(self) -> str:
        """Return a yt-dlp-resolvable URL for the channel's uploads listing."""
        if self.channel_id:
            return f"https://www.youtube.com/channel/{self.channel_id}/videos"
        if self.handle:
            if self.handle.startswith("@"):
                return f"https://www.youtube.com/{self.handle}/videos"
            return f"https://www.youtube.com/c/{self.handle}/videos"
        raise URLParseError("Channel reference has neither a handle nor a channel id")


class PlaylistURL(BaseModel):
    kind: Literal["playlist"] = "playlist"
    playlist_id: str


class VideoURL(BaseModel):
    kind: Literal["video"] = "video"
    video_id: str


ParsedURL = Annotated[ChannelURL | PlaylistURL | VideoURL, Field(discriminator="kind")]


def _strip_scheme_host(raw: str) -> tuple[str, str, dict[str, list[str]]]:
    candidate = raw if "//" in raw else f"https://{raw}"
    split = urlsplit(candidate)
    host = split.netloc.lower()
    return host, split.path, parse_qs(split.query)


def _parse_bare(raw: str) -> ParsedURL | None:
    if raw.startswith("@"):
        return ChannelURL(handle=raw.split("/", 1)[0])
    if _CHANNEL_ID_RE.match(raw):
        return ChannelURL(channel_id=raw)
    if _PLAYLIST_ID_RE.match(raw):
        return PlaylistURL(playlist_id=raw)
    if _VIDEO_ID_RE.match(raw):
        return VideoURL(video_id=raw)
    return None


def parse(url: str) -> ParsedURL:
    """Parse a YouTube URL or bare identifier into the discriminated union.

    Args:
        url: A channel, playlist, or video URL, or a bare ``@handle``, channel id,
            playlist id, or 11-character video id.

    Returns:
        One of :class:`ChannelURL`, :class:`PlaylistURL`, or :class:`VideoURL`.

    Raises:
        URLParseError: If the input is empty or not recognizable as YouTube.
    """
    raw = url.strip()
    if not raw:
        raise URLParseError("Empty URL")

    if "/" not in raw and "." not in raw:
        bare = _parse_bare(raw)
        if bare is not None:
            return bare

    if raw.startswith("@"):
        return ChannelURL(handle=raw.split("/", 1)[0])

    host, path, query = _strip_scheme_host(raw)
    if not any(host == h or host.endswith(f".{h}") for h in _YOUTUBE_HOSTS):
        raise URLParseError(f"Not a recognized YouTube URL: {url!r}")

    if host.endswith("youtu.be"):
        video_id = path.strip("/").split("/", 1)[0]
        if _VIDEO_ID_RE.match(video_id):
            return VideoURL(video_id=video_id)
        raise URLParseError(f"Malformed youtu.be link: {url!r}")

    segments = [segment for segment in path.split("/") if segment]
    first = segments[0] if segments else ""

    if first == "watch" and "v" in query:
        return VideoURL(video_id=query["v"][0])
    if first == "playlist" and "list" in query:
        return PlaylistURL(playlist_id=query["list"][0])
    if first in {"shorts", "embed", "live", "v"} and len(segments) >= 2:
        return VideoURL(video_id=segments[1])
    if first.startswith("@"):
        return ChannelURL(handle=first)
    if first == "channel" and len(segments) >= 2:
        return ChannelURL(channel_id=segments[1])
    if first in {"c", "user"} and len(segments) >= 2:
        return ChannelURL(handle=segments[1])

    # A lone query like ?v=ID without a /watch path, or ?list=ID.
    if "v" in query:
        return VideoURL(video_id=query["v"][0])
    if "list" in query:
        return PlaylistURL(playlist_id=query["list"][0])

    raise URLParseError(f"Could not classify YouTube URL: {url!r}")
