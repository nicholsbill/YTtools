# Tools

YTtools is a set of tools over one shared transcript store. All eight tools are
implemented: Fetch, Search, Summarize, Compare, Quotes, Timeline, Blog, and Ask.

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
- Stores per-video stats (view, like, and comment counts), duration, publish
  date, tags, and chapters alongside the transcript. Stats are a snapshot from
  fetch time; refresh them with "Metadata only" + `--refresh` (no transcript
  re-download).
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

## Compare

Compare how 2-5 channels cover overlapping ground.

```bash
yttools compare UC_channel_a UC_channel_b UC_channel_c
```

- Shared vs. unique topics (topics are extracted on first use), distinctive
  vocabulary per channel (TF-IDF), and when each channel covered shared topics.
- The web view (`/compare`) tabs between topic overlap, vocabulary, and timing.

## Timeline

See when topics rose and fell across a channel.

```bash
yttools timeline CHANNEL_ID                          # auto-discover top topics
yttools timeline CHANNEL_ID --mode specific --topic rust --topic "web assembly"
```

- Auto mode aggregates the topic tables by month; specific mode matches the
  named topics against transcripts.
- The web view (`/timeline`) renders a stacked-area chart plus a stats table.

## Ask

Local retrieval-augmented question answering over a channel.

```bash
yttools ask index CHANNEL_ID                 # build the embedding index (Ollama)
yttools ask query "what does the host think about X?" --channel CHANNEL_ID
```

- Indexing chunks and embeds each transcript locally with Ollama and stores the
  vectors in `chunk_embeddings`. Re-running skips already-indexed videos unless
  `--force` is passed.
- A query embeds the question, retrieves the nearest chunks (cosine similarity,
  reranked by recency), and returns an answer that cites its sources; each `[n]`
  marker links to the moment it came from.
- The embedding step always runs locally (Ollama); only the answer step uses the
  configured default provider, so switching providers does not require
  re-indexing.

All eight tools now ship. Remaining work is v1.0.0 polish (documentation site,
coverage, performance, and accessibility passes).
