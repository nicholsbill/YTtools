# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Blog now writes an *original* piece in a style you choose (a `style` field /
  `--style` flag — e.g. "a movie review by a film critic", "a TV newscaster
  reporting on it") instead of reformatting the transcript. The transcript is
  treated as source material, the model writes in the requested voice (not the
  speakers'), and timestamp links move to an optional "Key moments" footer rather
  than a forced section per transcript chunk. The `tone` field is renamed `style`.

## [0.2.0] - 2026-05-24

### Added

- Ask is now an analysis agent, not just retrieval. The model drives a JSON tool
  loop over your data — it can search and count videos, pull per-video and
  per-channel stats, compare videos, and search transcript content — so it can
  answer questions like "how many steak challenges did each channel do?" or
  "why did one video get more views than another?" with figures computed from
  the database (not guessed). It has a `top_videos` tool for best/worst
  questions, treats an empty search as "top videos" rather than erroring, and
  skips repeated identical tool calls so it does not waste steps. The UI shows
  the steps it took. Works with any provider via JSON mode; metadata questions
  work without an embedding index.

- Video stats are now visible and exportable. Fetch already stored view and like
  counts; this adds `comment_count`, shows views/likes/comments in search results
  and the transcript view, and includes a stats header in the `.txt` and `.md`
  exports (JSON already carried the full record). Re-running Fetch with
  "Metadata only" + "Force refresh" updates the counts without re-downloading
  transcripts.

- Hosted LLM providers are now wired: Anthropic, OpenAI, and Gemini implement
  completion, streaming, and JSON mode by calling each vendor's REST API
  directly (no vendor SDKs). Health checks list available models. Embeddings
  remain local via Ollama (the hosted `embed()` calls are unsupported except
  OpenAI's).
- Blog tool: convert a stored video transcript into a Markdown article whose
  section headers deep-link back to the matching YouTube timestamps. Available
  in the web UI (`/blog`), via the API (`POST /api/blog`), and the CLI
  (`yttools blog VIDEO_ID`).
- Summarize tool: structured channel digests with four types — overview
  (map-reduce), topics (per-video extraction then fuzzy clustering, persisted to
  the topics tables), guests (per-video interview extraction), and cadence (pure
  SQL, no model). Results are cached and reused unless regenerated. Web
  (`/summarize`), API (`POST /api/summarize`), and CLI (`yttools summarize`).
- Quotes tool: extract quotable lines (statement, prediction, stat, claim, list)
  from a channel or single video, de-duplicated and timestamp-linked, with CSV,
  JSON, and Markdown export. Web (`/quotes`), API (`POST /api/quotes`), and CLI
  (`yttools quotes`).
- Compare tool: compare 2-5 channels by shared/unique topics (fuzzy-aligned),
  distinctive vocabulary (TF-IDF across channel corpora), and when each channel
  covered shared topics. Reuses persisted topics, auto-extracting them if
  missing. Web (`/compare`), API (`POST /api/compare`), CLI (`yttools compare`).
- Timeline tool: topic-over-time view for a channel, either auto-discovered from
  the topic tables or tracking free-text topics matched against transcripts.
  Renders a stacked-area chart (Chart.js) plus per-topic timing stats. Web
  (`/timeline`), API (`POST /api/timeline`), CLI (`yttools timeline`).
- Ask tool: local retrieval-augmented question answering over a channel. Indexing
  chunks and embeds transcripts (locally via Ollama) into a portable
  `chunk_embeddings` table; queries embed the question, retrieve the nearest
  chunks by cosine similarity reranked with recency, and return a cited answer
  whose `[n]` markers link to the source moments. Web (`/ask`), API
  (`POST /api/ask`, `POST /api/ask/index`), CLI (`yttools ask index` / `query`).
- Settings page highlights the active model provider with an "Active" badge.
- Fetch can authenticate to YouTube with cookies to clear the "sign in to
  confirm you're not a bot" gate. Configure `youtube.cookies_from_browser` or
  `youtube.cookies_file` (browser wins if both set) and `youtube.sleep_requests`
  from the Settings page or `yttools config set`.

### Changed

- The AI tools (Blog, Summarize, Quotes, Compare, Timeline, Ask) now run as
  background jobs: the request returns a `job_id` immediately, the work keeps
  running even if you navigate to another tool, and the UI shows a live
  progress readout (per-video/per-step) by polling `GET /api/jobs/{id}`.
  Returning to a tool reconnects to its in-flight job, and finished results are
  retrievable. Tools accept an optional `on_progress` callback. Each running AI
  job can be cancelled from the UI (`POST /api/jobs/{id}/cancel`).
- Fetch retries the bot-check gate with backoff and sleeps briefly between
  yt-dlp requests, recovering most intermittent failures without setup.
- The default fetch concurrency dropped from 3 to 2 parallel videos to reduce
  the chance of being flagged as a bot.

### Fixed

- Search now honors the channel filter. The `channel` query parameter was typed
  as a plain `list[str]`, which FastAPI treated as a request body, so the
  selected channels were ignored and every channel was searched. It is now a
  proper repeatable query parameter.
- The Settings model field is now a dropdown whenever a model list is available,
  instead of a free-text input that password managers (1Password, LastPass)
  decorate with overlapping icons. The configured model stays selectable even if
  it is not in the fetched list; the text-input fallback opts out of autofill.
- Fetch no longer marks a video as an error with "Requested format is not
  available" when yt-dlp cannot select a media format. Because YTtools only
  needs metadata and captions, the metadata and caption calls now pass
  `--ignore-no-formats-error`, so subtitles are still extracted.

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
