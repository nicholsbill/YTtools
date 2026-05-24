# YTtools

> Local-first, open-source toolkit for turning any public YouTube channel, playlist, or video into a searchable, queryable knowledge base.

[![PyPI](https://img.shields.io/pypi/v/yttools.svg)](https://pypi.org/project/yttools/)
[![CI](https://github.com/nicholsbill/YTtools/actions/workflows/ci.yml/badge.svg)](https://github.com/nicholsbill/YTtools/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](./LICENSE)

YTtools runs as a single Python process and opens a browser UI at `http://localhost:8765`. No cloud services, no API keys required out of the box, no telemetry. Everything stays on your machine.

## What it does

Eight tools share one local storage layer (SQLite):

- **Fetch** — download transcripts and metadata from public YouTube URLs.
- **Search** — full-text search across every transcript, with jump-links to the original timestamps.
- **Summarize** — structured digests of a channel: topics, recurring themes, guests, posting cadence.
- **Compare** — side-by-side analysis of channels covering the same beat.
- **Quotes** — extract quotable lines, classified by type.
- **Timeline** — topic-over-time view across a channel.
- **Blog** — convert a single video into a structured Markdown article with timestamp citations.
- **Ask** — local retrieval-augmented question answering over a channel, with cited answers.

Fetch, Search, Summarize, Compare, Quotes, Timeline, and Blog work today. Ask (local RAG) lands in a later release (see the roadmap).

## Why local-first

- **Privacy** — transcripts, queries, and generated content never leave your machine.
- **No subscriptions** — the default AI backend is a local [Ollama](https://ollama.com) install. No metered API.
- **No lock-in** — your data is a single SQLite file you own.

## Install

```bash
pipx install yttools
yttools serve
```

The web UI opens automatically at `http://localhost:8765`. Plain `pip install yttools` works too if you prefer to manage the environment yourself.

`yt-dlp` is installed as a dependency. For the AI tools, install [Ollama](https://ollama.com) and pull a model (for example `ollama pull llama3.1:8b`), or supply an API key for a hosted provider in Settings.

## Quick start

```bash
# Pull a channel's transcripts and metadata into the local database
yttools fetch https://www.youtube.com/@TED

# Search across everything fetched
yttools search "machine learning" --limit 20

# Launch the browser UI
yttools serve
```

## YouTube cookies (when fetch is blocked)

YouTube sometimes answers an unauthenticated request with `Sign in to confirm
you're not a bot`. Fetch already retries the gated request with a short backoff
and pauses briefly between requests, which clears most intermittent cases. If it
keeps happening, give YTtools cookies so its requests are authenticated. You can
set this on the **Settings** page under "YouTube fetching", or from the CLI.

There are two ways to supply cookies. If you set both, the browser source wins.

### Option A — read cookies from your browser

Point YTtools at a browser you are already logged into YouTube with. Nothing to
export; yt-dlp reads the cookies directly.

```bash
yttools config set youtube.cookies_from_browser firefox
```

Supported values: `chrome`, `chromium`, `firefox`, `safari`, `brave`, `edge`,
`opera`, `vivaldi`. Notes:

- Firefox tends to be the most reliable. Recent Chrome versions encrypt cookies
  in a way that can block extraction, and on macOS Chrome may need to be fully
  quit (it locks its cookie database while running).
- The browser must have an active, logged-in YouTube session.

### Option B — use an exported cookies.txt file

Export your YouTube cookies once to a Netscape-format `cookies.txt`, then point
YTtools at the file.

1. Install a "cookies.txt" exporter browser extension (search your browser's
   add-on store for one that exports the Netscape format).
2. Log into YouTube, then use the extension to export cookies for
   `youtube.com` to a file, for example `~/.yttools/cookies.txt`.
3. Tell YTtools where it is:

   ```bash
   yttools config set youtube.cookies_file ~/.yttools/cookies.txt
   ```

A `cookies.txt` holds your logged-in YouTube session. Treat it like a password:
keep it outside any repository and do not share it. Cookies expire, so re-export
the file if the bot check returns.

### Tuning

```bash
# Seconds yt-dlp waits between requests (default 1.0; set 0 to disable)
yttools config set youtube.sleep_requests 1.5

# Parallel downloads (default 2; lower values are less likely to be flagged)
yttools config set fetch.concurrent_videos 1
```

## AI features

The AI-backed tools default to a local Ollama model, so they work offline with no account. If you prefer a hosted model, three are supported (Anthropic, OpenAI, Gemini): add an API key on the Settings page or via the matching environment variable, then use "Test connection" to confirm it and load the model picker. Keys resolve in this order: config file value, then environment variable. Without a key, that provider stays disabled. See [docs/llm-providers.md](./docs/llm-providers.md) for the supported providers and their models.

## Roadmap

Releases are tracked on [GitHub milestones](https://github.com/nicholsbill/YTtools/milestones). v0.1.0 covers the storage layer, Fetch, and Search. v0.2.0 adds Summarize, Quotes, and Blog. v0.3.0 adds Compare, Timeline, and Ask.

## Contributing

Bug reports, feature requests, and Pull Requests are welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md) for setup, coding standards, and the Contributor License Agreement process.

## License

Dual-licensed:

- **[AGPL-3.0](./LICENSE)** for open-source use. If you modify YTtools or use it to provide a network service, you must publish your changes under AGPL-3.0.
- **[Commercial license](./LICENSE-COMMERCIAL.md)** for organizations whose use is incompatible with AGPL-3.0.

Contributors sign a [Contributor License Agreement](./CLA_INDIVIDUAL.md) on their first PR.
