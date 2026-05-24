# AGENTS.md

Context for contributors working in this repo, automated or otherwise. Read this
before making changes.

## Architecture

YTtools is a single Python process that serves a local web UI (FastAPI + uvicorn)
and exposes the same actions through a Typer CLI. All persistent state lives in a
single SQLite file under `~/.yttools/` (override with `YTTOOLS_HOME`).

Layering, from the bottom up:

- `core/` — shared infrastructure with no tool-specific logic: database access and
  migrations (`db.py`), Pydantic models (`models.py`), URL parsing (`urls.py`),
  yt-dlp wrappers (`youtube.py`), transcript parsing (`transcripts.py`), exporters
  (`exports.py`), the SSE progress bus (`progress.py`), the LLM provider
  abstraction (`llm.py`), and embedding helpers (`embeddings.py`).
- `tools/` — one module per user-facing tool. Tools depend on `core/`, never the
  reverse. All eight ship: `fetch.py`, `search.py`, `summarize.py`, `compare.py`,
  `quotes.py`, `timeline.py`, `blog.py`, and `ask.py`. Compare and Timeline reuse
  `summarize.ensure_channel_topics` to populate the topic tables on first use.
  Ask embeds transcripts into `chunk_embeddings` (a portable table, brute-force
  cosine search) and always embeds via Ollama regardless of the answer provider.
- `web/` — the FastAPI app factory, route handlers (`routes/`), Jinja2 templates,
  and static assets.
- `cli.py` — Typer commands, one per tool plus `serve`, `config`, `db`, `version`.

Eight tools are planned (Fetch, Search, Summarize, Compare, Quotes, Timeline,
Blog, Ask). They share the storage layer. See the database schema in
`core/migrations/` for the canonical data model.

## Style

- **Ruff** is the source of truth for linting and formatting. Run `ruff check .`
  and `ruff format --check .` before committing. Line length is 100.
- **mypy** runs in strict mode on `src/yttools/`. Run `mypy src/`.
- **pytest** for tests. Run `pytest` (or `make test`).
- **Pydantic v2** models throughout. No bare dicts in public function signatures.
- **Async I/O** everywhere. Subprocess and HTTP calls use async APIs.
- Docstrings on public functions and classes, Google-style.
- A `Makefile` wraps the common commands: `make dev`, `make test`, `make check`,
  `make format`, `make serve`.

## Conventions

- SQLite access goes through `core/db.py` only. No raw `sqlite3.connect` elsewhere.
- LLM access goes through the provider abstraction in `core/llm.py` only.
- No blocking I/O in route handlers. yt-dlp and provider HTTP calls are invoked
  with async subprocess / async HTTP and bounded concurrency.
- yt-dlp invocations always pass `--no-warnings` and use `--skip-download` for
  metadata-only calls.
- Schema changes are new numbered migration files under `core/migrations/`. Never
  edit an applied migration; add a new one.
- Commit messages follow Conventional Commits (see CONTRIBUTING.md).

## Hard constraints

These are enforced and checked before release:

- Every Python source file starts with the two-line SPDX + copyright header:
  ```python
  # SPDX-License-Identifier: AGPL-3.0-or-later
  # Copyright (C) 2025 William Nichols and YTtools contributors
  ```
- No telemetry, analytics, auto-update checks, or remote logging. The app runs
  fully offline apart from the YouTube and LLM calls the user initiates.
- Never log transcripts, full timestamped URLs, or LLM prompts/responses at INFO
  or above. DEBUG only, and only when explicitly enabled.
- No emojis in code, comments, docstrings, logs, or commit messages. UI status
  badges may use Unicode glyphs (the `✓ ○ — ● △` set) where they carry meaning.
- External AI/tool vendor names appear only where functionally required: LLM
  provider class names in `core/llm.py`, provider labels in the Settings UI, and
  `docs/llm-providers.md`. Nowhere else, and never as attribution.
- A banned-vocabulary list is enforced in prose (README, docs, comments, UI copy,
  error messages). Keep writing plain and direct.

## What to avoid

- New top-level dependencies without a clear, justified need.
- Pinning minor versions in `pyproject.toml` unless required for compatibility.
- Tests that require network access. Mock yt-dlp subprocess calls and LLM HTTP.

## Local-only files

The original design spec and the one-time `bootstrap.sh` setup script are kept out
of version control (see `.git/info/exclude`); they are build inputs, not shipped
artifacts. `OPERATOR_NOTES.md` is gitignored for the same reason.

## Decisions and gotchas

Record non-obvious choices here as they are made.

- **Package name:** `yttools` (confirmed available on PyPI at build time).
- **Env var names for API keys:** the hosted-provider keys follow the conventional
  `<PROVIDER>_API_KEY` form. The exact names live in `docs/llm-providers.md`.
- **LLM providers:** all four are wired. Each hosted provider (Anthropic,
  OpenAI, Gemini) calls its vendor REST API directly over httpx (no vendor
  SDKs); `_HostedProvider` owns the client lifecycle, concurrency gate, and the
  shared POST/GET/SSE helpers. JSON mode uses each vendor's native feature
  (OpenAI `response_format`, Gemini `responseMimeType`) or, for Anthropic, a
  system-prompt instruction. `health_check()` short-circuits to unavailable when
  no key is set (no network) and otherwise lists models via the vendor's models
  endpoint. Embeddings: Ollama and OpenAI/Gemini support them; Anthropic does
  not (its `embed()` raises `LLMError`, and callers fall back to Ollama).
- **Database connection:** one `sqlite3` connection opened with
  `check_same_thread=False` and guarded by a lock. Async callers wrap DB calls in
  `asyncio.to_thread`. This is plenty for a local single-user app and sidesteps
  "database is locked" churn.
- **sqlite-vec `chunks` table:** sqlite-vec 0.1.x rejects `REAL` for auxiliary
  columns (use `float`) and the table is created in code, not in the SQL
  migration, since it needs a loadable extension. Its creation is wrapped so any
  incompatibility disables vector features rather than breaking the schema.
- **Caption source:** yt-dlp output does not reliably distinguish manual from
  auto captions, so transcripts are stored with `is_auto_generated=True`.
- **YouTube bot gate:** YouTube intermittently answers with "sign in to confirm
  you're not a bot". `core/youtube.py` classifies this as `BotCheckError`, and
  `_run_ytdlp` retries it with linear backoff (`_run_ytdlp_once` is the single,
  non-retrying call). `YouTubeOptions` carries cookie and `--sleep-requests`
  flags into every invocation, built from the `[youtube]` config section by
  `fetch.youtube_options_from_settings`. Cookies are the reliable fix; the
  browser source wins over a cookies file when both are set. Default fetch
  concurrency is 2 for the same reason.
- **AI-tool jobs:** the AI tools run as background tasks so they survive client
  navigation. `api._start_job` stores `{status, progress, result, detail}` in
  `app.state.job_results` and runs the tool in `app.state.tasks`; the browser
  polls `GET /api/jobs/{id}`. This is separate from Fetch, which uses the
  SSE progress bus. Each tool entry function takes an optional
  `on_progress: ProgressCallback` (`core/progress.report` is the no-op-safe
  caller); the CLI passes `None`, so it stays synchronous. The browser remembers
  the active job id per tool in `localStorage` to reconnect after navigation.
- **`--ignore-no-formats-error`:** the metadata and caption calls pass this flag.
  Even with `--skip-download`, yt-dlp runs media format selection, and for some
  videos/clients it aborts with "Requested format is not available". We only
  need metadata and subtitles, so the flag lets extraction continue. Verified:
  forcing `-f 999` reproduces the error, and adding the flag still writes the
  VTT.
- **Settings save:** the web `/api/settings` handler writes through the raw config
  file (`config.set_config_value`), never through env-resolved `Settings`, so an
  API key supplied via an environment variable is never copied to `config.toml`.
- **Front end:** Tailwind and Alpine load from a CDN (no build step, per spec).
  The UI degrades to unstyled-but-functional when offline.
- **SSE timing:** the fetch UI POSTs to start a job, then opens the EventSource.
  Early per-video events can be missed if the job outruns the subscription, but
  the terminal `job_done` event always carries the full summary.
- **Where vendor provider names may appear:** only `core/llm.py` (provider
  classes), `config.py` (config schema and default model identifiers),
  `web/templates/settings.html` (UI labels), and `docs/llm-providers.md`.
  `config.py` is functionally required for the config plumbing and is the one
  location beyond the spec's stated three. Tests reference providers indirectly
  through `PROVIDER_NAMES` and `HOSTED_PROVIDER_CLASSES` so vendor names stay out
  of the test suite. The word "cursor" in the source is always a SQLite cursor or
  an offset variable, never the editor.
- **CI coverage gate:** the gate is core-only. CI runs
  `pytest -o addopts="" --cov=yttools.core --cov-fail-under=70` so it measures
  `core/` rather than the whole package (the default `addopts` covers everything).
- **Packaging:** hatchling includes the `templates/`, `static/`, and
  `migrations/` data files because they live inside the package tree. A
  fresh-venv install of the built wheel was verified to serve the UI and run the
  CLI with no source checkout present.
