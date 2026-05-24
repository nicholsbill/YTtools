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
    from yttools.tools.fetch import FetchConfig, FetchJob, youtube_options_from_settings

    settings = load_settings()
    config = FetchConfig(
        include_transcripts=not no_transcripts,
        languages=lang,
        force_refresh=refresh,
        concurrent_videos=settings.fetch.concurrent_videos,
    )

    async def runner() -> None:
        database = _open_db()
        from yttools.core.progress import get_bus

        bus = get_bus()
        job = FetchJob(
            database,
            urls,
            config,
            bus=bus,
            captions_dir=settings.home_dir / "captions",
            youtube_options=youtube_options_from_settings(settings),
        )
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


@app.command()
def blog(
    video_id: str = typer.Argument(..., help="Stored video id to convert."),
    length: str = typer.Option("medium", "--length", help="short, medium, or long."),
    tone: str = typer.Option("", "--tone", help="Target tone (default: match the speaker)."),
    title: str = typer.Option("", "--title", help="Override the article title."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write Markdown to a file."),
) -> None:
    """Convert a stored video transcript into a Markdown article."""
    from pathlib import Path

    from yttools.core.llm import get_provider
    from yttools.tools.blog import BlogError, generate_blog

    if length not in {"short", "medium", "long"}:
        typer.echo("--length must be short, medium, or long", err=True)
        raise typer.Exit(code=1)

    settings = load_settings()
    provider = get_provider(settings)
    database = _open_db()
    try:
        result = asyncio.run(
            generate_blog(
                database,
                provider,
                video_id,
                tone=tone or None,
                length=length,  # type: ignore[arg-type]
                title_override=title or None,
            )
        )
    except BlogError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    finally:
        database.close()

    if output:
        Path(output).write_text(result.markdown, encoding="utf-8")
        typer.echo(f"Wrote {result.word_count} words to {output}")
    else:
        typer.echo(result.markdown)


@app.command()
def summarize(
    channel_id: str = typer.Argument(..., help="Channel id to summarize."),
    types: list[str] = typer.Option(
        ["overview", "cadence"], "--type", help="overview, topics, guests, cadence."
    ),
    force: bool = typer.Option(False, "--force", help="Ignore cached summaries."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write Markdown to a file."),
) -> None:
    """Generate a structured digest of a channel."""
    from pathlib import Path

    from yttools.core.llm import get_provider
    from yttools.tools.summarize import SummarizeError, summarize_channel

    settings = load_settings()
    provider = get_provider(settings)
    database = _open_db()
    try:
        result = asyncio.run(
            summarize_channel(database, provider, channel_id, summary_types=types, force=force)
        )
    except SummarizeError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    finally:
        database.close()

    body = "\n\n".join(section.content for section in result.sections)
    if output:
        Path(output).write_text(body, encoding="utf-8")
        typer.echo(f"Wrote {output}")
    else:
        typer.echo(body)


@app.command()
def quotes(
    source_id: str = typer.Argument(..., help="Channel id, or a video id with --video."),
    video: bool = typer.Option(False, "--video", help="Treat the id as a single video."),
    types: list[str] = typer.Option([], "--type", help="Restrict to quote types."),
    fmt: str = typer.Option("md", "--format", help="md, csv, or json."),
    regenerate: bool = typer.Option(False, "--regenerate", help="Re-extract via the model."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write output to a file."),
) -> None:
    """Extract quotable lines from a channel or single video."""
    from pathlib import Path

    from yttools.core.llm import get_provider
    from yttools.tools.quotes import QuotesError, export_quotes, extract_quotes, load_quotes

    settings = load_settings()
    provider = get_provider(settings)
    database = _open_db()
    try:
        video_ids = [source_id] if video else [v.id for v in database.list_videos(source_id)]
        if not video_ids:
            typer.echo("No videos found for that source", err=True)
            raise typer.Exit(code=1)
        result = load_quotes(database, video_ids, types or None)
        if regenerate or not result.total:
            result = asyncio.run(
                extract_quotes(database, provider, video_ids=video_ids, quote_types=types or None)
            )
        body, _ = export_quotes(result, fmt)
    except QuotesError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    finally:
        database.close()

    if output:
        Path(output).write_text(body, encoding="utf-8")
        typer.echo(f"Wrote {output}")
    else:
        typer.echo(body)


@app.command()
def compare(
    channel_ids: list[str] = typer.Argument(..., help="2-5 channel ids to compare."),
) -> None:
    """Compare channels by shared/unique topics and distinctive vocabulary."""
    from yttools.core.llm import get_provider
    from yttools.tools.compare import CompareError, compare_channels

    settings = load_settings()
    provider = get_provider(settings)
    database = _open_db()
    try:
        result = asyncio.run(compare_channels(database, provider, channel_ids))
    except CompareError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    finally:
        database.close()

    typer.echo("Shared topics:")
    for shared in result.shared_topics:
        typer.echo(f"  {shared.label} — {', '.join(shared.channels)}")
    typer.echo("\nDistinctive vocabulary:")
    for channel in result.channels:
        terms = ", ".join(t.term for t in result.vocabulary.get(channel.id, [])[:10])
        typer.echo(f"  {channel.title}: {terms}")


@app.command()
def timeline(
    channel_id: str = typer.Argument(..., help="Channel id."),
    mode: str = typer.Option("auto", "--mode", help="auto or specific."),
    topics: list[str] = typer.Option([], "--topic", help="Topics to track (specific mode)."),
) -> None:
    """Show when topics rose and fell across a channel."""
    from yttools.core.llm import get_provider
    from yttools.tools.timeline import TimelineError, build_timeline

    settings = load_settings()
    provider = get_provider(settings)
    database = _open_db()
    try:
        result = asyncio.run(
            build_timeline(database, provider, channel_id, mode=mode, topics=topics)
        )
    except TimelineError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    finally:
        database.close()

    typer.echo(f"{len(result.stats)} topic(s) across {len(result.months)} month(s)")
    for stat in result.stats[:20]:
        typer.echo(
            f"  {stat.topic}: {stat.total} video(s) "
            f"({stat.first_month} -> {stat.last_month}, peak {stat.peak_month})"
        )


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
