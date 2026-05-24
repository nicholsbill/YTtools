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
