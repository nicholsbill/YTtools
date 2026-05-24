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

## Planned

- **Summarize, Quotes, Blog** (v0.2.0): structured digests, quote extraction, and
  transcript-to-article conversion.
- **Compare, Timeline, Ask** (v0.3.0): cross-channel comparison, topic timelines,
  and retrieval-augmented question answering over a channel.
