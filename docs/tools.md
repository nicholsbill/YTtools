# Tools

YTtools is a set of tools over one shared transcript store. Two ship in v0.1.0;
the rest are planned for later releases.

## Fetch (v0.1.0)

Downloads metadata and transcripts from public YouTube URLs.

```bash
yttools fetch https://www.youtube.com/@TED
yttools fetch URL1 URL2 --no-transcripts
yttools fetch URL --refresh --lang en
```

- Accepts channel, playlist, and video URLs, plus bare identifiers, mixed freely.
- Re-runs skip videos that already have a transcript unless `--refresh` is passed
  or the stored copy is more than seven days old.
- Private, deleted, members-only, and live videos are logged and skipped.
- Captions are still extracted from videos that offer no downloadable media
  format, so a "Requested format is not available" yt-dlp error no longer fails
  the video.

### When YouTube asks you to "confirm you're not a bot"

YouTube sometimes gates unauthenticated requests with `Sign in to confirm
you're not a bot`. Fetch already retries the gated request a couple of times and
sleeps briefly between requests, which clears most intermittent cases. If it
keeps happening, supply cookies so requests are authenticated. Set either in the
Settings page or from the CLI:

```bash
# Read cookies from a logged-in browser (chrome, firefox, safari, brave, edge, ...)
yttools config set youtube.cookies_from_browser chrome

# Or point to an exported Netscape-format cookies.txt
yttools config set youtube.cookies_file ~/.yttools/cookies.txt

# Tune the politeness delay (seconds between requests; 0 disables it)
yttools config set youtube.sleep_requests 1.5
```

If both cookie sources are set, the browser source wins. Lowering
`fetch.concurrent_videos` (default 2) also reduces the chance of being flagged.

## Search (v0.1.0)

Full-text search across every stored transcript, ranked by BM25.

```bash
yttools search "machine learning"
yttools search 'crypto AND NOT regulation' --channel UCxxxx --limit 20
yttools search "pyth*" --json
```

- Query syntax: phrases in double quotes, the boolean operators `AND`/`OR`/`NOT`,
  and prefix matches with `*`.
- Results link to the exact YouTube timestamp of each match.
- Filter by channel, publish date range, and video length.

## Blog

Turn a single stored video transcript into a publishable Markdown article.

```bash
yttools blog VIDEO_ID --length medium --output article.md
yttools blog VIDEO_ID --tone "plain and direct" --title "My title"
```

- Uses the default configured model provider (local Ollama or a hosted provider
  with an API key set in Settings).
- The model returns a title and sections; each section header gets a
  `[Watch this section]` link to its YouTube timestamp.
- Length presets target roughly 800 (short), 1500 (medium), or 2400 (long) words.
- In the web UI (`/blog`), pick a fetched video and see a side-by-side rendered
  preview and raw Markdown, with a download button.

## Summarize

Structured digest of a channel.

```bash
yttools summarize CHANNEL_ID --type overview --type topics --type cadence
```

- Types: `overview` (map-reduce synthesis), `topics` (per-video labels clustered
  and ranked), `guests` (interview guests), `cadence` (posting rate, gaps, median
  length — computed without a model).
- Topics are persisted so Compare and Timeline can reuse them.
- Results are cached in the database; pass `--force` (or check "Force
  regenerate" in the UI) to recompute.

## Quotes

Extract quotable lines from a channel or single video.

```bash
yttools quotes CHANNEL_ID --type stat --type prediction --format csv -o quotes.csv
yttools quotes VIDEO_ID --video --regenerate
```

- Quote types: statement, prediction, stat, claim, list.
- Each quote links to its timestamp; near-duplicates are merged.
- Export as CSV, JSON, or Markdown (web and CLI).

## Planned

- **Compare, Timeline, Ask** (v0.3.0): cross-channel comparison, topic timelines,
  and retrieval-augmented question answering over a channel.
