# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""JSON API backing the UI.

Database work runs in worker threads so the event loop never blocks. Settings
writes operate on the raw config file, never on env-resolved values, so API keys
supplied through the environment are never copied to disk.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from yttools import config as config_module
from yttools.config import load_settings
from yttools.core import exports
from yttools.core.db import Database
from yttools.core.llm import build_provider, build_providers
from yttools.tools.fetch import FetchConfig, FetchJob
from yttools.tools.search import SearchError, SearchFilters, search

router = APIRouter(prefix="/api")


class FetchRequest(BaseModel):
    urls: list[str]
    include_transcripts: bool = True
    languages: list[str] = Field(default_factory=lambda: ["en"])
    force_refresh: bool = False


class ProviderSettingUpdate(BaseModel):
    api_key: str | None = None
    default_model: str | None = None


class SettingsUpdate(BaseModel):
    default_provider: str | None = None
    ollama_base_url: str | None = None
    providers: dict[str, ProviderSettingUpdate] = Field(default_factory=dict)


def _db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


@router.get("/stats")
async def stats(request: Request) -> dict[str, Any]:
    database = _db(request)

    def collect() -> dict[str, Any]:
        last = database.last_fetch_time()
        return {
            "video_count": database.count_videos(),
            "active_jobs": database.count_active_jobs(),
            "last_fetch": last.isoformat() if last else None,
        }

    return await asyncio.to_thread(collect)


@router.get("/channels")
async def channels(request: Request) -> list[dict[str, Any]]:
    database = _db(request)
    rows = await asyncio.to_thread(database.list_channels)
    return [{"id": channel.id, "title": channel.title} for channel in rows]


@router.post("/fetch")
async def start_fetch(request: Request, payload: FetchRequest) -> dict[str, str]:
    urls = [line.strip() for line in payload.urls if line.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="No URLs provided")
    settings = request.app.state.settings
    config = FetchConfig(
        include_transcripts=payload.include_transcripts,
        languages=payload.languages or ["en"],
        force_refresh=payload.force_refresh,
        concurrent_videos=settings.fetch.concurrent_videos,
    )
    job = FetchJob(
        _db(request),
        urls,
        config,
        bus=request.app.state.bus,
        captions_dir=settings.home_dir / "captions",
    )
    request.app.state.jobs[job.job_id] = job

    async def runner() -> None:
        try:
            await job.run()
        finally:
            request.app.state.jobs.pop(job.job_id, None)

    task = asyncio.ensure_future(runner())
    request.app.state.tasks.add(task)
    task.add_done_callback(request.app.state.tasks.discard)
    return {"job_id": job.job_id}


@router.post("/fetch/{job_id}/cancel")
async def cancel_fetch(request: Request, job_id: str) -> dict[str, bool]:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such active job")
    job.cancel()
    return {"cancelled": True}


@router.get("/search")
async def search_endpoint(
    request: Request,
    q: str,
    channel: list[str] | None = None,
    published_after: str | None = None,
    published_before: str | None = None,
    min_minutes: float | None = None,
    max_minutes: float | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    filters = SearchFilters(
        channel_ids=channel or [],
        published_after=published_after,
        published_before=published_before,
        min_duration_minutes=min_minutes,
        max_duration_minutes=max_minutes,
    )
    database = _db(request)
    try:
        response = await asyncio.to_thread(
            search, database, q, filters=filters, limit=limit, offset=offset
        )
    except SearchError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return response.model_dump(mode="json")


@router.get("/video/{video_id}")
async def video_detail(request: Request, video_id: str) -> dict[str, Any]:
    database = _db(request)
    video = await asyncio.to_thread(database.get_video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Unknown video")
    transcript = await asyncio.to_thread(database.get_transcript, video_id)
    return {
        "video": video.model_dump(mode="json"),
        "transcript": transcript.model_dump(mode="json") if transcript else None,
    }


@router.get("/export/{video_id}")
async def export_transcript(
    request: Request, video_id: str, fmt: exports.ExportFormat = "txt"
) -> PlainTextResponse:
    database = _db(request)
    video = await asyncio.to_thread(database.get_video, video_id)
    transcript = await asyncio.to_thread(database.get_transcript, video_id)
    if video is None or transcript is None:
        raise HTTPException(status_code=404, detail="No transcript for that video")
    body = exports.render(fmt, video, transcript)
    return PlainTextResponse(
        body,
        headers={"Content-Disposition": f'attachment; filename="{video_id}.{fmt}"'},
    )


@router.get("/providers")
async def providers(request: Request) -> list[dict[str, Any]]:
    settings = request.app.state.settings
    built = build_providers(settings)
    results: list[dict[str, Any]] = []
    for name, provider in built.items():
        health = await provider.health_check()
        api_key = _provider_api_key(settings, name)
        results.append(
            {
                "name": name,
                "available": health.available,
                "message": health.message,
                "models": health.models,
                "default_model": getattr(provider, "default_model", ""),
                "key_set": bool(api_key),
                "key_masked": _mask(api_key),
                "functional": name == "ollama",
            }
        )
    return results


@router.post("/providers/{name}/test")
async def test_provider(request: Request, name: str) -> dict[str, Any]:
    settings = load_settings()
    request.app.state.settings = settings
    try:
        provider = build_provider(name, settings)
    except Exception as error:  # unknown provider name
        raise HTTPException(status_code=404, detail=str(error)) from error
    health = await provider.health_check()
    return health.model_dump()


@router.post("/settings")
async def save_settings(request: Request, payload: SettingsUpdate) -> dict[str, bool]:
    def apply() -> None:
        if payload.default_provider:
            config_module.set_config_value("llm.default_provider", payload.default_provider)
        if payload.ollama_base_url:
            config_module.set_config_value("llm.ollama.base_url", payload.ollama_base_url)
        for name, update in payload.providers.items():
            if update.api_key is not None:
                config_module.set_config_value(f"llm.{name}.api_key", update.api_key)
            if update.default_model:
                config_module.set_config_value(f"llm.{name}.default_model", update.default_model)

    await asyncio.to_thread(apply)
    request.app.state.settings = load_settings()
    return {"saved": True}


def _provider_api_key(settings: Any, name: str) -> str:
    if name == "ollama":
        return ""
    provider_config = getattr(settings.llm, name, None)
    return getattr(provider_config, "api_key", "") if provider_config else ""


def _mask(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 4:
        return "•" * len(api_key)
    return f"{'•' * (len(api_key) - 4)}{api_key[-4:]}"
