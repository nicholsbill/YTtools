# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the FastAPI web layer using the in-process test client."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    "path",
    [
        "/",
        "/fetch",
        "/search",
        "/settings",
        "/blog",
        "/summarize",
        "/quotes",
        "/compare",
        "/timeline",
        "/ask",
    ],
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


def test_search_channel_filter_applies(client: TestClient) -> None:
    _seed(client)  # UC_x / vid00000001, transcript mentions "machine learning"
    database: Database = client.app.state.db
    database.upsert_channel(Channel(id="UC_other", title="Other"))
    database.upsert_video(Video(id="vidother001", channel_id="UC_other", title="Other ML"))
    database.upsert_transcript(
        Transcript(
            video_id="vidother001",
            language="en",
            is_auto_generated=True,
            text="another take on machine learning entirely",
        )
    )
    # Both channels match the query…
    assert client.get("/api/search", params={"q": "machine"}).json()["total"] == 2
    # …but filtering by one channel returns only that channel's video.
    filtered = client.get("/api/search", params={"q": "machine", "channel": "UC_x"}).json()
    assert filtered["total"] == 1
    assert filtered["results"][0]["video_id"] == "vid00000001"


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


def _run_job(
    client: TestClient, url: str, body: dict[str, Any], *, timeout: float = 5.0
) -> dict[str, Any]:
    """Start a background AI job and poll until it reaches a terminal state."""
    start = client.post(url, json=body)
    assert start.status_code == 200, start.text
    job_id = start.json()["job_id"]
    deadline = time.time() + timeout
    entry: dict[str, Any] = {}
    while time.time() < deadline:
        entry = client.get(f"/api/jobs/{job_id}").json()
        if entry["status"] != "running":
            return entry
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish: {entry}")


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
    entry = _run_job(client, "/api/blog", {"video_id": "vid00000001", "length": "short"})
    assert entry["status"] == "done"
    data = entry["result"]
    assert data["title"] == "My Article"
    assert "## Intro" in data["markdown"]
    assert "watch?v=vid00000001&t=12s" in data["markdown"]
    assert data["word_count"] > 0


def test_blog_unknown_video_errors(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("yttools.web.routes.api.get_provider", lambda settings: _FakeProvider("{}"))
    entry = _run_job(client, "/api/blog", {"video_id": "missing"})
    assert entry["status"] == "error"
    assert "not in the database" in entry["detail"]


def test_summarize_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(client)
    monkeypatch.setattr("yttools.web.routes.api.get_provider", lambda settings: _FakeProvider("{}"))
    entry = _run_job(client, "/api/summarize", {"channel_id": "UC_x", "summary_types": ["cadence"]})
    assert entry["status"] == "done"
    sections = entry["result"]["sections"]
    assert sections and sections[0]["summary_type"] == "cadence"


def test_quotes_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(client)
    payload = '{"quotes": [{"text": "Insightful line", "type": "statement", "start_seconds": 12}]}'
    monkeypatch.setattr(
        "yttools.web.routes.api.get_provider", lambda settings: _FakeProvider(payload)
    )
    entry = _run_job(
        client, "/api/quotes", {"source": "video", "id": "vid00000001", "regenerate": True}
    )
    assert entry["status"] == "done"
    data = entry["result"]
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
    entry = _run_job(client, "/api/compare", {"channel_ids": ["UC_a", "UC_b"]})
    assert entry["status"] == "done"
    assert len(entry["result"]["channels"]) == 2


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
    entry = _run_job(
        client,
        "/api/timeline",
        {"channel_id": "UC_t", "mode": "specific", "topics": ["machine learning"]},
    )
    assert entry["status"] == "done"
    data = entry["result"]
    assert data["mode"] == "specific"
    assert "2024-03" in data["months"]


class _FakeEmbed:
    name = "emb"
    default_model = "e"

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        return [[1.0, 0.0, 1.0] for _ in texts]


class _ScriptedProvider:
    name = "fake"
    default_model = "fake-1"

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls = 0

    async def complete(self, prompt: str, **kwargs: object) -> str:
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


def test_ask_index_and_query(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(client)
    monkeypatch.setattr("yttools.web.routes.api.embedding_provider", lambda settings: _FakeEmbed())
    scripted = _ScriptedProvider(
        [
            json.dumps({"tool": "content_search", "args": {"query": "what is this"}}),
            json.dumps({"answer": "It covers machine learning [1]."}),
        ]
    )
    monkeypatch.setattr("yttools.web.routes.api.get_provider", lambda settings: scripted)

    indexed = _run_job(client, "/api/ask/index", {"channel_id": "UC_x"})
    assert indexed["status"] == "done"
    assert indexed["result"]["chunks_indexed"] >= 1

    status = client.get("/api/ask/status", params={"channel": "UC_x"}).json()
    assert status["indexed_chunks"] >= 1

    answered = _run_job(client, "/api/ask", {"question": "what is this?", "channel_ids": ["UC_x"]})
    assert answered["status"] == "done"
    data = answered["result"]
    assert data["citations"]
    assert data["steps"]  # a tool was used
    assert "](https://www.youtube.com/watch?v=vid00000001" in data["answer"]


def test_ask_answers_without_index(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(client)
    monkeypatch.setattr("yttools.web.routes.api.embedding_provider", lambda settings: _FakeEmbed())
    scripted = _ScriptedProvider(
        [
            json.dumps({"tool": "channel_stats", "args": {"channel": "UC_x"}}),
            json.dumps({"answer": "That channel has 1 video."}),
        ]
    )
    monkeypatch.setattr("yttools.web.routes.api.get_provider", lambda settings: scripted)
    # No index built; a metadata question still completes via the data tools.
    entry = _run_job(client, "/api/ask", {"question": "how many videos?", "channel_ids": ["UC_x"]})
    assert entry["status"] == "done"
    assert "1 video" in entry["result"]["answer"]


def test_cancel_unknown_job_returns_404(client: TestClient) -> None:
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_cancel_running_job(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(client)

    class _BlockingProvider:
        name = "fake"
        default_model = "m"

        async def complete(self, prompt: str, **kwargs: object) -> str:
            await asyncio.Event().wait()  # never resolves until cancelled
            return "{}"

    monkeypatch.setattr("yttools.web.routes.api.get_provider", lambda settings: _BlockingProvider())
    job_id = client.post("/api/blog", json={"video_id": "vid00000001"}).json()["job_id"]

    # Wait until the job is blocked inside the model call, then cancel it.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        progress = client.get(f"/api/jobs/{job_id}").json()["progress"]
        if progress["message"] == "Generating the article":
            break
        time.sleep(0.02)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200

    entry: dict[str, Any] = {}
    while time.time() < deadline:
        entry = client.get(f"/api/jobs/{job_id}").json()
        if entry["status"] != "running":
            break
        time.sleep(0.02)
    assert entry["status"] == "cancelled"


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
