# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Typer command-line interface.

Every web UI action has a CLI equivalent so the tool can be scripted. Commands are
added per release; v0.1.0 ships ``fetch``, ``search``, ``list``, ``serve``,
``config``, ``db``, and ``version``.
"""

from __future__ import annotations

import asyncio

import typer

from yttools import config as config_module
from yttools.config import load_settings
from yttools.core.db import Database
from yttools.version import __version__

app = typer.Typer(
    name="yttools",
    help="Local-first toolkit for searching public YouTube transcripts.",
    no_args_is_help=True,
    add_completion=False,
)

config_app = typer.Typer(help="Read and write configuration values.", no_args_is_help=True)
app.add_typer(config_app, name="config")

db_app = typer.Typer(help="Database maintenance commands.", no_args_is_help=True)
app.add_typer(db_app, name="db")


def _open_db() -> Database:
    return Database.open(load_settings().db_path)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Dotted key, e.g. llm.default_provider"),
) -> None:
    """Print a configuration value."""
    try:
        value = config_module.get_config_value(key)
    except KeyError:
        typer.echo(f"Unknown config key: {key}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(str(value))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Dotted key, e.g. llm.default_provider"),
    value: str = typer.Argument(..., help="New value"),
) -> None:
    """Set a configuration value and persist it to config.toml."""
    config_module.set_config_value(key, value)
    typer.echo(f"Set {key} = {value}")


@app.command()
def fetch(
    urls: list[str] = typer.Argument(..., help="Channel, playlist, or video URLs."),
    no_transcripts: bool = typer.Option(False, "--no-transcripts", help="Metadata only."),
    refresh: bool = typer.Option(False, "--refresh", help="Re-fetch even if already stored."),
    lang: list[str] = typer.Option(["en"], "--lang", help="Preferred caption languages."),
) -> None:
    """Download transcripts and metadata for one or more YouTube URLs."""
    from yttools.tools.fetch import FetchConfig, FetchJob

    config = FetchConfig(
        include_transcripts=not no_transcripts, languages=lang, force_refresh=refresh
    )
    settings = load_settings()

    async def runner() -> None:
        database = _open_db()
        from yttools.core.progress import get_bus

        bus = get_bus()
        job = FetchJob(database, urls, config, bus=bus, captions_dir=settings.home_dir / "captions")
        queue = await bus.subscribe(job.job_id)
        task = asyncio.ensure_future(job.run())
        while True:
            event = await queue.get()
            if event is None:
                break
            if event.event == "video_update":
                data = event.data
                title = data.get("title") or ""
                typer.echo(f"[{data.get('state'):>17}] {data.get('video_id')}  {title}")
        summary = await task
        typer.echo(
            f"\nDone: {summary.done}  Skipped: {summary.skipped}  "
            f"No captions: {summary.no_captions}  Errors: {summary.errors}"
        )
        database.close()

    asyncio.run(runner())


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query (phrase, boolean, or prefix syntax)."),
    channel: list[str] = typer.Option([], "--channel", help="Restrict to channel id(s)."),
    limit: int = typer.Option(50, "--limit", help="Maximum results to return."),
    json_output: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Search transcripts and print ranked matches with timestamp links."""
    from yttools.tools.search import SearchError, SearchFilters
    from yttools.tools.search import search as run_search

    database = _open_db()
    try:
        response = run_search(
            database, query, filters=SearchFilters(channel_ids=channel), limit=limit
        )
    except SearchError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    finally:
        database.close()

    if json_output:
        typer.echo(response.model_dump_json(indent=2))
        return
    typer.echo(f"{response.total} result(s) for {query!r}\n")
    for result in response.results:
        typer.echo(f"{result.title}")
        typer.echo(f"  {result.url}")
        typer.echo(f"  {result.snippet}\n")


@app.command("list")
def list_items(
    kind: str = typer.Argument(..., help="channels, playlists, or videos."),
    channel: str | None = typer.Option(None, "--channel", help="Filter videos by channel id."),
) -> None:
    """List stored channels, playlists, or videos."""
    database = _open_db()
    try:
        if kind == "channels":
            for row in database.list_channels():
                typer.echo(f"{row.id}\t{row.title}")
        elif kind == "playlists":
            for playlist in database.list_playlists():
                typer.echo(f"{playlist.id}\t{playlist.title}")
        elif kind == "videos":
            for video in database.list_videos(channel):
                typer.echo(f"{video.id}\t{video.title}")
        else:
            typer.echo("kind must be one of: channels, playlists, videos", err=True)
            raise typer.Exit(code=1)
    finally:
        database.close()


@app.command()
def serve(
    host: str | None = typer.Option(None, "--host", help="Bind address."),
    port: int | None = typer.Option(None, "--port", help="Bind port."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev)."),
) -> None:
    """Start the local web UI."""
    import uvicorn

    from yttools.web.app import open_browser_when_ready

    settings = load_settings()
    bind_host = host or settings.server.host
    bind_port = port or settings.server.port
    if settings.server.open_browser and not no_browser:
        open_browser_when_ready(f"http://{bind_host}:{bind_port}")
    uvicorn.run(
        "yttools.web.app:create_app",
        factory=True,
        host=bind_host,
        port=bind_port,
        reload=reload,
    )


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply any unapplied database migrations."""
    database = _open_db()
    applied = database.migrate()
    database.close()
    typer.echo(f"Applied {len(applied)} migration(s).")


@db_app.command("backup")
def db_backup() -> None:
    """Write a timestamped copy of the database file."""
    import shutil
    from datetime import UTC, datetime

    settings = load_settings()
    source = settings.db_path
    if not source.exists():
        typer.echo("No database to back up yet.", err=True)
        raise typer.Exit(code=1)
    database = _open_db()
    database._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    database.close()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = source.with_name(f"yttools.backup-{stamp}.db")
    shutil.copy2(source, target)
    typer.echo(f"Backed up to {target}")


@db_app.command("reset")
def db_reset(
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Delete the database and recreate an empty schema."""
    settings = load_settings()
    if not yes:
        typer.confirm(f"This deletes {settings.db_path} and all stored data. Continue?", abort=True)
    for suffix in ("", "-wal", "-shm"):
        candidate = settings.db_path.with_name(settings.db_path.name + suffix)
        candidate.unlink(missing_ok=True)
    _open_db().close()
    typer.echo("Database reset.")


def main() -> None:
    """Console-script entry point."""
    app()
