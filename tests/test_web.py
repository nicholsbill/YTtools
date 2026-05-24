# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the FastAPI web layer using the in-process test client."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from yttools.config import Settings
from yttools.core import youtube
from yttools.core.db import Database
from yttools.core.llm import PROVIDER_NAMES
from yttools.core.models import Channel, Segment, Transcript, Video
from yttools.web.app import create_app

LOCAL_PROVIDER = PROVIDER_NAMES[0]
HOSTED_PROVIDER = next(name for name in PROVIDER_NAMES if name != LOCAL_PROVIDER)


@pytest.fixture
def client(tmp_home: Path) -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _seed(client: TestClient) -> None:
    database: Database = client.app.state.db
    database.upsert_channel(Channel(id="UC_x", title="Example Channel"))
    database.upsert_video(
        Video(id="vid00000001", channel_id="UC_x", title="A Talk", duration_seconds=600)
    )
    database.upsert_transcript(
        Transcript(
            video_id="vid00000001",
            language="en",
            is_auto_generated=True,
            text="we discuss machine learning and vector search",
            segments=[
                Segment(start=12.0, end=18.0, text="we discuss machine learning and vector search")
            ],
        )
    )


@pytest.mark.parametrize(
    "path", ["/", "/fetch", "/search", "/settings", "/blog", "/summarize", "/quotes"]
)
def test_pages_render(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert "YTtools" in response.text


def test_stats_empty(client: TestClient) -> None:
    data = client.get("/api/stats").json()
    assert data["video_count"] == 0
    assert data["active_jobs"] == 0


def test_channels_after_seed(client: TestClient) -> None:
    _seed(client)
    channels = client.get("/api/channels").json()
    assert channels == [{"id": "UC_x", "title": "Example Channel"}]


def test_search_endpoint(client: TestClient) -> None:
    _seed(client)
    data = client.get("/api/search", params={"q": "machine"}).json()
    assert data["total"] == 1
    result = data["results"][0]
    assert result["video_id"] == "vid00000001"
    assert "t=12s" in result["url"]


def test_search_invalid_query_returns_400(client: TestClient) -> None:
    response = client.get("/api/search", params={"q": "   "})
    assert response.status_code == 400


def test_video_detail_and_export(client: TestClient) -> None:
    _seed(client)
    detail = client.get("/api/video/vid00000001").json()
    assert detail["transcript"]["language"] == "en"

    export = client.get("/api/export/vid00000001", params={"fmt": "md"})
    assert export.status_code == 200
    assert "# A Talk" in export.text
    assert "attachment" in export.headers["content-disposition"]


def test_export_unknown_format_rejected(client: TestClient) -> None:
    _seed(client)
    response = client.get("/api/export/vid00000001", params={"fmt": "pdf"})
    assert response.status_code == 422  # FastAPI rejects the invalid literal


def test_export_missing_video_404(client: TestClient) -> None:
    response = client.get("/api/export/missing", params={"fmt": "txt"})
    assert response.status_code == 404


def test_providers_listed(client: TestClient) -> None:
    providers = client.get("/api/providers").json()
    names = {p["name"] for p in providers}
    assert names == set(PROVIDER_NAMES)
    # Every provider's completion path is wired now.
    assert all(p["functional"] is True for p in providers)


def test_settings_save_persists(client: TestClient, tmp_home: Path) -> None:
    model = getattr(Settings().llm, HOSTED_PROVIDER).default_model
    response = client.post(
        "/api/settings",
        json={
            "default_provider": LOCAL_PROVIDER,
            "providers": {HOSTED_PROVIDER: {"default_model": model}},
        },
    )
    assert response.json() == {"saved": True}
    assert (tmp_home / "config.toml").exists()


def test_settings_save_does_not_write_env_keys(
    client: TestClient, tmp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_var = f"{HOSTED_PROVIDER.upper()}_API_KEY"
    monkeypatch.setenv(env_var, "sk-secret-from-env")
    client.post("/api/settings", json={"default_provider": LOCAL_PROVIDER})
    config_text = (tmp_home / "config.toml").read_text(encoding="utf-8")
    assert "sk-secret-from-env" not in config_text


def test_youtube_settings_roundtrip(client: TestClient, tmp_home: Path) -> None:
    initial = client.get("/api/youtube-settings").json()
    assert initial["cookies_from_browser"] == ""
    assert initial["sleep_requests"] == 1.0

    saved = client.post(
        "/api/settings",
        json={"youtube_cookies_from_browser": "chrome", "youtube_sleep_requests": 2.0},
    )
    assert saved.json() == {"saved": True}

    updated = client.get("/api/youtube-settings").json()
    assert updated["cookies_from_browser"] == "chrome"
    assert updated["sleep_requests"] == 2.0
    assert 'cookies_from_browser = "chrome"' in (tmp_home / "config.toml").read_text(
        encoding="utf-8"
    )


class _FakeProvider:
    name = "fake"
    default_model = "fake-1"

    def __init__(self, payload: str) -> None:
        self._payload = payload

    async def complete(self, prompt: str, **kwargs: object) -> str:
        return self._payload


def test_videos_reports_transcript_flag(client: TestClient) -> None:
    _seed(client)
    videos = client.get("/api/videos").json()
    assert any(v["id"] == "vid00000001" and v["has_transcript"] for v in videos)


def test_blog_generates_article(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(client)
    payload = (
        '{"title": "My Article", "sections": '
        '[{"heading": "Intro", "start_seconds": 12, "markdown": "Body text."}]}'
    )
    monkeypatch.setattr(
        "yttools.web.routes.api.get_provider", lambda settings: _FakeProvider(payload)
    )
    response = client.post("/api/blog", json={"video_id": "vid00000001", "length": "short"})
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "My Article"
    assert "## Intro" in data["markdown"]
    assert "watch?v=vid00000001&t=12s" in data["markdown"]
    assert data["word_count"] > 0


def test_blog_unknown_video_returns_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("yttools.web.routes.api.get_provider", lambda settings: _FakeProvider("{}"))
    response = client.post("/api/blog", json={"video_id": "missing"})
    assert response.status_code == 400


def test_summarize_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(client)
    monkeypatch.setattr("yttools.web.routes.api.get_provider", lambda settings: _FakeProvider("{}"))
    response = client.post(
        "/api/summarize", json={"channel_id": "UC_x", "summary_types": ["cadence"]}
    )
    assert response.status_code == 200
    sections = response.json()["sections"]
    assert sections and sections[0]["summary_type"] == "cadence"


def test_quotes_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(client)
    payload = '{"quotes": [{"text": "Insightful line", "type": "statement", "start_seconds": 12}]}'
    monkeypatch.setattr(
        "yttools.web.routes.api.get_provider", lambda settings: _FakeProvider(payload)
    )
    response = client.post(
        "/api/quotes", json={"source": "video", "id": "vid00000001", "regenerate": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert "watch?v=vid00000001&t=12s" in data["quotes"][0]["url"]


def test_compare_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    database: Database = client.app.state.db
    for cid, title, text in [
        ("UC_a", "Alpha", "neural networks and deep learning models"),
        ("UC_b", "Beta", "recipes and cooking and baking in the kitchen"),
    ]:
        database.upsert_channel(Channel(id=cid, title=title))
        database.upsert_video(Video(id=f"{cid}_v", channel_id=cid, title=title))
        database.upsert_transcript(
            Transcript(video_id=f"{cid}_v", language="en", is_auto_generated=True, text=text)
        )
    monkeypatch.setattr("yttools.web.routes.api.get_provider", lambda settings: _FakeProvider("{}"))
    response = client.post("/api/compare", json={"channel_ids": ["UC_a", "UC_b"]})
    assert response.status_code == 200
    assert len(response.json()["channels"]) == 2


def test_timeline_specific_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    database: Database = client.app.state.db
    database.upsert_channel(Channel(id="UC_t", title="T"))
    database.upsert_video(
        Video(id="vt1", channel_id="UC_t", title="t", published_at=datetime(2024, 3, 1, tzinfo=UTC))
    )
    database.upsert_transcript(
        Transcript(
            video_id="vt1",
            language="en",
            is_auto_generated=True,
            text="we explore machine learning here",
        )
    )
    monkeypatch.setattr("yttools.web.routes.api.get_provider", lambda settings: _FakeProvider("{}"))
    response = client.post(
        "/api/timeline",
        json={"channel_id": "UC_t", "mode": "specific", "topics": ["machine learning"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "specific"
    assert "2024-03" in data["months"]


def test_start_fetch_returns_job_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_runner(args: list[str], *, timeout: float = youtube.DEFAULT_TIMEOUT):
        return 0, "", "ERROR: Private video"

    monkeypatch.setattr(youtube, "_run_ytdlp", fake_runner)
    response = client.post("/api/fetch", json={"urls": ["dQw4w9WgXcQ"]})
    assert response.status_code == 200
    assert "job_id" in response.json()


def test_start_fetch_rejects_empty(client: TestClient) -> None:
    response = client.post("/api/fetch", json={"urls": []})
    assert response.status_code == 400
