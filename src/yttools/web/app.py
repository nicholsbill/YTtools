# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""FastAPI application factory.

The app runs migrations on startup, holds the shared database connection and
progress bus on ``app.state``, and serves the server-rendered UI. It binds to
localhost only and ships no CORS headers: the design is single-user and local.
"""

from __future__ import annotations

import threading
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from yttools.config import Settings, load_settings
from yttools.core.db import Database
from yttools.core.progress import ProgressBus, get_bus

_PACKAGE_DIR = Path(__file__).parent
TEMPLATES_DIR = _PACKAGE_DIR / "templates"
STATIC_DIR = _PACKAGE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class AppState:
    """Typed container for objects stored on ``app.state``."""

    db: Database
    settings: Settings
    bus: ProgressBus
    jobs: dict[str, object]


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database.open(resolved.db_path)
        app.state.db = database
        app.state.settings = resolved
        app.state.bus = get_bus()
        app.state.jobs = {}
        app.state.tasks = set()
        # Background AI-tool jobs: job_id -> {status, progress, result, detail}.
        app.state.job_results = {}
        try:
            yield
        finally:
            database.close()

    app = FastAPI(title="YTtools", version=_version(), lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from yttools.web.routes import api, pages, sse

    app.include_router(pages.router)
    app.include_router(api.router)
    app.include_router(sse.router)
    return app


def _version() -> str:
    from yttools.version import __version__

    return __version__


def open_browser_when_ready(url: str, *, delay: float = 1.0) -> None:
    """Open the default browser to the app once the server has had time to bind."""

    def _open() -> None:
        webbrowser.open(url)

    timer = threading.Timer(delay, _open)
    timer.daemon = True
    timer.start()
