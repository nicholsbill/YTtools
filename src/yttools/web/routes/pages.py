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
    {"key": "summarize", "label": "Summarize", "href": "#", "enabled": False},
    {"key": "compare", "label": "Compare", "href": "#", "enabled": False},
    {"key": "quotes", "label": "Quotes", "href": "#", "enabled": False},
    {"key": "timeline", "label": "Timeline", "href": "#", "enabled": False},
    {"key": "blog", "label": "Blog", "href": "#", "enabled": False},
    {"key": "ask", "label": "Ask", "href": "#", "enabled": False},
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


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    return _render(request, "settings.html", "settings")
