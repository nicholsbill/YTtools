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

Fetch and Search ship in v0.1.0. The AI-backed tools land in v0.2.0 and v0.3.0 (see the roadmap).

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

## AI features

The AI-backed tools default to a local Ollama model, so they work offline with no account. If you prefer a hosted model, three hosted providers are supported: add an API key on the Settings page or via the matching environment variable. Keys resolve in this order: config file value, then environment variable. Without a key, that provider stays disabled. See [docs/llm-providers.md](./docs/llm-providers.md) for the list of supported providers and their models.

## Roadmap

Releases are tracked on [GitHub milestones](https://github.com/nicholsbill/YTtools/milestones). v0.1.0 covers the storage layer, Fetch, and Search. v0.2.0 adds Summarize, Quotes, and Blog. v0.3.0 adds Compare, Timeline, and Ask.

## Contributing

Bug reports, feature requests, and Pull Requests are welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md) for setup, coding standards, and the Contributor License Agreement process.

## License

Dual-licensed:

- **[AGPL-3.0](./LICENSE)** for open-source use. If you modify YTtools or use it to provide a network service, you must publish your changes under AGPL-3.0.
- **[Commercial license](./LICENSE-COMMERCIAL.md)** for organizations whose use is incompatible with AGPL-3.0.

Contributors sign a [Contributor License Agreement](./CLA_INDIVIDUAL.md) on their first PR.
