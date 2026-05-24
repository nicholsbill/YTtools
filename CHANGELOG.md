# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-05-24

First public release: the storage layer, the Fetch tool, and the Search tool.

### Added

- Project scaffolding: AGPL-3.0 license, code of conduct, contribution guide,
  Contributor License Agreements, issue and Pull Request templates.
- `pyproject.toml` with hatchling builds, ruff, mypy (strict on `src/`), and
  pytest configuration.
- Core infrastructure:
  - SQLite storage layer with a numbered migration runner, WAL journaling, and
    foreign keys enforced. Full-text search via FTS5; an optional sqlite-vec
    vector table is created when the extension is available.
  - Pydantic v2 domain models for channels, playlists, videos, transcripts,
    quotes, summaries, topics, and jobs.
  - YouTube URL parsing for channel, playlist, and video forms plus bare
    identifiers.
  - Async yt-dlp wrappers for listing, metadata, and caption downloads, with
    typed errors for unavailable, members-only, and live videos.
  - WebVTT parsing that strips tags and speaker labels and collapses rolling
    caption duplication into timestamped segments.
  - Transcript exporters for `.txt`, `.md` (with timestamp links), `.srt`, and
    `.json`, plus a zip bundler.
  - An in-process publish/subscribe progress bus for server-sent events.
  - An LLM provider abstraction with a fully wired local Ollama provider and
    config-aware stubs for the three hosted providers (completion arrives in a
    later release).
- Fetch tool: a worker-pool job that downloads metadata and transcripts, skips
  already-fetched videos unless forced or stale, and reports per-video progress.
- Search tool: BM25-ranked full-text search with snippet highlighting,
  timestamp deep-links, and channel, date, and duration filters.
- Web UI: a FastAPI application serving Fetch, Search, and Settings pages with a
  server-sent-events progress feed; built with Jinja2, Tailwind, and Alpine.
- Command-line interface: `fetch`, `search`, `list`, `serve`, `config`, `db`,
  and `version`.
- Continuous integration across Linux, macOS, and Windows on Python 3.11 to
  3.13, and a tag-triggered release workflow that publishes to PyPI via trusted
  publishing.

[Unreleased]: https://github.com/nicholsbill/YTtools/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/nicholsbill/YTtools/releases/tag/v0.1.0
