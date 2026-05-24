# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for YouTube URL parsing."""

from __future__ import annotations

import pytest

from yttools.core.urls import (
    ChannelURL,
    PlaylistURL,
    URLParseError,
    VideoURL,
    parse,
)


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ&list=PLxyz", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=42", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ],
)
def test_parse_video(url: str, video_id: str) -> None:
    result = parse(url)
    assert isinstance(result, VideoURL)
    assert result.kind == "video"
    assert result.video_id == video_id


@pytest.mark.parametrize(
    ("url", "playlist_id"),
    [
        ("https://www.youtube.com/playlist?list=PLabcdef12345", "PLabcdef12345"),
        ("PLabcdefghij12345", "PLabcdefghij12345"),
        ("https://www.youtube.com/playlist?list=UUabcdefghij1", "UUabcdefghij1"),
    ],
)
def test_parse_playlist(url: str, playlist_id: str) -> None:
    result = parse(url)
    assert isinstance(result, PlaylistURL)
    assert result.playlist_id == playlist_id


def test_parse_channel_handle() -> None:
    for url in ("https://www.youtube.com/@TED", "https://www.youtube.com/@TED/videos", "@TED"):
        result = parse(url)
        assert isinstance(result, ChannelURL)
        assert result.handle == "@TED"
        assert result.channel_id is None


def test_parse_channel_id() -> None:
    channel_id = "UCsT0YIqwnpJCM-mx7-gSA4Q"
    for url in (
        f"https://www.youtube.com/channel/{channel_id}",
        f"https://www.youtube.com/channel/{channel_id}/videos",
        channel_id,
    ):
        result = parse(url)
        assert isinstance(result, ChannelURL)
        assert result.channel_id == channel_id


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/c/TEDTalks",
        "https://www.youtube.com/user/TEDtalksDirector",
    ],
)
def test_parse_legacy_custom_url(url: str) -> None:
    result = parse(url)
    assert isinstance(result, ChannelURL)
    assert result.handle is not None


def test_listing_target_for_channel_id() -> None:
    result = ChannelURL(channel_id="UCsT0YIqwnpJCM-mx7-gSA4Q")
    assert result.listing_target() == (
        "https://www.youtube.com/channel/UCsT0YIqwnpJCM-mx7-gSA4Q/videos"
    )


def test_listing_target_for_handle() -> None:
    assert ChannelURL(handle="@TED").listing_target() == "https://www.youtube.com/@TED/videos"
    assert ChannelURL(handle="TEDTalks").listing_target() == (
        "https://www.youtube.com/c/TEDTalks/videos"
    )


def test_listing_target_without_identifier_raises() -> None:
    with pytest.raises(URLParseError):
        ChannelURL().listing_target()


@pytest.mark.parametrize("bad", ["", "   ", "https://vimeo.com/12345", "not a url at all!"])
def test_unparseable_raises(bad: str) -> None:
    with pytest.raises(URLParseError):
        parse(bad)
