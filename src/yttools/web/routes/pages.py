# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Server-rendered HTML pages."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from yttools.core.db import Database
from yttools.version import __version__

router = APIRouter()

# Nav items shown in the sidebar. Tools beyond v0.1.0 are present but disabled.
NAV_ITEMS: list[dict[str, Any]] = [
    {"key": "fetch", "label": "Fetch", "href": "/fetch", "enabled": True},
    {"key": "search", "label": "Search", "href": "/search", "enabled": True},
    {"key": "summarize", "label": "Summarize", "href": "/summarize", "enabled": True},
    {"key": "compare", "label": "Compare", "href": "/compare", "enabled": True},
    {"key": "quotes", "label": "Quotes", "href": "/quotes", "enabled": True},
    {"key": "timeline", "label": "Timeline", "href": "/timeline", "enabled": True},
    {"key": "blog", "label": "Blog", "href": "/blog", "enabled": True},
    {"key": "ask", "label": "Ask", "href": "/ask", "enabled": True},
]


def _stats(database: Database) -> dict[str, Any]:
    last = database.last_fetch_time()
    return {
        "video_count": database.count_videos(),
        "active_jobs": database.count_active_jobs(),
        "last_fetch": last.strftime("%Y-%m-%d %H:%M") if last else "never",
    }


def _context(request: Request, active: str) -> dict[str, Any]:
    database: Database = request.app.state.db
    settings = request.app.state.settings
    return {
        "version": __version__,
        "nav_items": NAV_ITEMS,
        "active": active,
        "provider": settings.llm.default_provider,
        "stats": _stats(database),
    }


def _render(request: Request, template: str, active: str) -> HTMLResponse:
    from yttools.web.app import templates

    return templates.TemplateResponse(request, template, _context(request, active))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return _render(request, "fetch.html", "fetch")


@router.get("/fetch", response_class=HTMLResponse)
async def fetch_page(request: Request) -> HTMLResponse:
    return _render(request, "fetch.html", "fetch")


@router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request) -> HTMLResponse:
    return _render(request, "search.html", "search")


@router.get("/blog", response_class=HTMLResponse)
async def blog_page(request: Request) -> HTMLResponse:
    return _render(request, "blog.html", "blog")


@router.get("/summarize", response_class=HTMLResponse)
async def summarize_page(request: Request) -> HTMLResponse:
    return _render(request, "summarize.html", "summarize")


@router.get("/quotes", response_class=HTMLResponse)
async def quotes_page(request: Request) -> HTMLResponse:
    return _render(request, "quotes.html", "quotes")


@router.get("/compare", response_class=HTMLResponse)
async def compare_page(request: Request) -> HTMLResponse:
    return _render(request, "compare.html", "compare")


@router.get("/timeline", response_class=HTMLResponse)
async def timeline_page(request: Request) -> HTMLResponse:
    return _render(request, "timeline.html", "timeline")


@router.get("/ask", response_class=HTMLResponse)
async def ask_page(request: Request) -> HTMLResponse:
    return _render(request, "ask.html", "ask")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    return _render(request, "settings.html", "settings")
