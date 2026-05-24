# Architecture

YTtools is a single Python process. It serves a local web UI and exposes the same
actions through a command-line interface. All state lives in one SQLite file under
`~/.yttools/` (override with the `YTTOOLS_HOME` environment variable).

## Layers

```
cli.py / web/            user entry points (Typer commands, FastAPI routes)
  └── tools/             one module per user-facing tool (fetch, search, ...)
        └── core/        shared infrastructure (no tool-specific logic)
```

- **core/** holds the database access layer (`db.py`), Pydantic models
  (`models.py`), URL parsing (`urls.py`), yt-dlp wrappers (`youtube.py`),
  transcript parsing (`transcripts.py`), exporters (`exports.py`), the progress
  bus (`progress.py`), the LLM provider abstraction (`llm.py`), and embedding
  helpers (`embeddings.py`).
- **tools/** depends on core and never the other way around. v0.1.0 ships
  `fetch.py` and `search.py`.
- **web/** is a FastAPI application factory with page, JSON, and SSE routes, plus
  Jinja2 templates and static assets.

## Data flow

1. Fetch parses input URLs, lists videos with yt-dlp, downloads captions, parses
   them into timestamped segments, and writes channels, videos, and transcripts
   to SQLite. Transcript inserts populate an FTS5 index through table triggers.
2. Search turns a query into an FTS5 MATCH expression, ranks matches with BM25,
   and maps each snippet back to the nearest segment to build a timestamped link.

## Concurrency

The web layer is async. yt-dlp runs through `asyncio.create_subprocess_exec`, and
the fetch worker pool caps parallel video work (default three). The SQLite
connection is synchronous and shared behind a lock; async code calls it through
`asyncio.to_thread`, so route handlers never block the event loop.

## Storage

WAL journaling, foreign keys enforced. Schema changes are added as new numbered
SQL files under `core/migrations/` and applied on startup. The full schema is the
source of truth for the data model.
