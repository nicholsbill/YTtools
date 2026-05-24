// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2025 William Nichols and YTtools contributors
// Shared client-side helpers and Alpine component factories.

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function clock(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

// Convert the markdown-style **bold** match markers into highlighted spans.
function renderSnippet(snippet) {
  const escaped = escapeHtml(snippet);
  return escaped.replace(/\*\*(.+?)\*\*/g, '<mark class="bg-accent/30 rounded px-0.5">$1</mark>');
}

function statusBar(videoCount, activeJobs) {
  return {
    videoCount,
    activeJobs,
    start() {
      this.refresh();
      setInterval(() => this.refresh(), 5000);
    },
    async refresh() {
      try {
        const data = await (await fetch("/api/stats")).json();
        this.videoCount = data.video_count;
        this.activeJobs = data.active_jobs;
      } catch (_) {
        /* leave last known values on transient failure */
      }
    },
  };
}

function fetchPanel() {
  return {
    urls: "",
    includeTranscripts: "true",
    languages: "en",
    forceRefresh: false,
    running: false,
    error: "",
    rows: [],
    completed: 0,
    total: 0,
    jobId: null,
    source: null,
    badge(state) {
      return {
        done: "✓",
        skipped: "○",
        "no-captions": "△",
        error: "●",
        fetching_metadata: "…",
        fetching_transcript: "…",
        queued: "—",
      }[state] || "—";
    },
    upsertRow(data) {
      const existing = this.rows.find((row) => row.video_id === data.video_id);
      if (existing) {
        existing.state = data.state;
        existing.message = data.message;
        existing.title = data.title || existing.title;
      } else {
        this.rows.push({ ...data });
      }
    },
    async start() {
      this.error = "";
      this.rows = [];
      this.completed = 0;
      this.total = 0;
      const urls = this.urls.split("\n").map((s) => s.trim()).filter(Boolean);
      const languages = this.languages.split(",").map((s) => s.trim()).filter(Boolean);
      if (!urls.length) {
        this.error = "Add at least one URL.";
        return;
      }
      this.running = true;
      let response;
      try {
        response = await fetch("/api/fetch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            urls,
            include_transcripts: this.includeTranscripts === "true",
            languages,
            force_refresh: this.forceRefresh,
          }),
        });
      } catch (e) {
        this.error = "Could not reach the server.";
        this.running = false;
        return;
      }
      if (!response.ok) {
        this.error = (await response.json()).detail || "Fetch failed.";
        this.running = false;
        return;
      }
      this.jobId = (await response.json()).job_id;
      this.source = new EventSource(`/api/jobs/${this.jobId}/events`);
      this.source.addEventListener("video_update", (e) => {
        const event = JSON.parse(e.data);
        this.upsertRow(event.data);
        this.completed = event.current;
        this.total = event.total;
      });
      const finish = () => {
        this.running = false;
        if (this.source) this.source.close();
      };
      this.source.addEventListener("job_done", finish);
      this.source.addEventListener("job_cancelled", finish);
      this.source.onerror = () => finish();
    },
    async cancel() {
      if (!this.jobId) return;
      await fetch(`/api/fetch/${this.jobId}/cancel`, { method: "POST" });
    },
  };
}

function searchPanel() {
  return {
    query: "",
    channels: [],
    channelIds: [],
    publishedAfter: "",
    publishedBefore: "",
    minMinutes: "",
    maxMinutes: "",
    results: [],
    total: 0,
    searched: false,
    error: "",
    transcript: null,
    clock,
    render: renderSnippet,
    async loadChannels() {
      try {
        this.channels = await (await fetch("/api/channels")).json();
      } catch (_) {
        this.channels = [];
      }
    },
    highlight(text) {
      const escaped = escapeHtml(text);
      const terms = this.query.replace(/["*()]/g, " ").split(/\s+/)
        .filter((t) => t && !["AND", "OR", "NOT", "NEAR"].includes(t));
      let output = escaped;
      for (const term of terms) {
        const pattern = new RegExp(`(${term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi");
        output = output.replace(pattern, '<mark class="bg-accent/30 rounded px-0.5">$1</mark>');
      }
      return output;
    },
    async run() {
      this.error = "";
      this.searched = true;
      const params = new URLSearchParams();
      params.set("q", this.query);
      for (const id of this.channelIds) params.append("channel", id);
      if (this.publishedAfter) params.set("published_after", this.publishedAfter);
      if (this.publishedBefore) params.set("published_before", this.publishedBefore);
      if (this.minMinutes) params.set("min_minutes", this.minMinutes);
      if (this.maxMinutes) params.set("max_minutes", this.maxMinutes);
      const response = await fetch(`/api/search?${params.toString()}`);
      if (!response.ok) {
        this.error = (await response.json()).detail || "Search failed.";
        this.results = [];
        this.total = 0;
        return;
      }
      const data = await response.json();
      this.results = data.results;
      this.total = data.total;
    },
    async openTranscript(videoId) {
      this.transcript = await (await fetch(`/api/video/${videoId}`)).json();
    },
  };
}

function settingsPanel(currentDefault) {
  return {
    providers: [],
    defaultProvider: currentDefault || "ollama",
    ollamaBaseUrl: "http://localhost:11434",
    browsers: ["chrome", "chromium", "firefox", "safari", "brave", "edge", "opera", "vivaldi"],
    youtube: { cookies_from_browser: "", cookies_file: "", sleep_requests: 1.0 },
    saved: false,
    statusGlyph(provider) {
      return provider.available ? "●" : "○";
    },
    async load() {
      this.providers = await (await fetch("/api/providers")).json();
      this.providers.forEach((p) => {
        if (!p.api_key) p.api_key = "";
      });
      this.youtube = await (await fetch("/api/youtube-settings")).json();
    },
    async test(name) {
      const health = await (await fetch(`/api/providers/${name}/test`, { method: "POST" })).json();
      const provider = this.providers.find((p) => p.name === name);
      if (provider) {
        provider.available = health.available;
        provider.message = health.message;
        provider.models = health.models;
      }
    },
    async save() {
      this.saved = false;
      const providers = {};
      for (const p of this.providers) {
        providers[p.name] = {
          default_model: p.default_model || null,
          api_key: p.name !== "ollama" && p.api_key ? p.api_key : null,
        };
      }
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          default_provider: this.defaultProvider,
          ollama_base_url: this.ollamaBaseUrl,
          providers,
          youtube_cookies_from_browser: this.youtube.cookies_from_browser,
          youtube_cookies_file: this.youtube.cookies_file,
          youtube_sleep_requests: this.youtube.sleep_requests,
        }),
      });
      this.saved = true;
      setTimeout(() => (this.saved = false), 2000);
    },
  };
}

function renderMarkdown(md) {
  if (window.marked && typeof window.marked.parse === "function") {
    return window.marked.parse(md);
  }
  return `<pre class="whitespace-pre-wrap">${escapeHtml(md)}</pre>`;
}

function blogPanel() {
  return {
    videos: [],
    videoId: "",
    tone: "",
    length: "medium",
    title: "",
    running: false,
    error: "",
    markdown: "",
    rendered: "",
    modelUsed: "",
    wordCount: 0,
    async loadVideos() {
      try {
        this.videos = await (await fetch("/api/videos")).json();
      } catch (_) {
        this.videos = [];
      }
    },
    async convert() {
      if (!this.videoId) return;
      this.error = "";
      this.running = true;
      this.markdown = "";
      this.rendered = "";
      this.wordCount = 0;
      try {
        const response = await fetch("/api/blog", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            video_id: this.videoId,
            tone: this.tone,
            length: this.length,
            title: this.title,
          }),
        });
        if (!response.ok) {
          this.error = (await response.json()).detail || "Conversion failed.";
          return;
        }
        const data = await response.json();
        this.markdown = data.markdown;
        this.rendered = renderMarkdown(data.markdown);
        this.modelUsed = data.model_used || "";
        this.wordCount = data.word_count || 0;
      } catch (_) {
        this.error = "Could not reach the server.";
      } finally {
        this.running = false;
      }
    },
    download() {
      if (!this.markdown) return;
      downloadText(this.markdown, `${this.videoId || "article"}.md`, "text/markdown");
    },
  };
}

function downloadText(text, filename, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function summarizePanel() {
  return {
    channels: [],
    channelId: "",
    allTypes: ["overview", "topics", "guests", "cadence"],
    types: ["overview", "cadence"],
    force: false,
    running: false,
    error: "",
    sections: [],
    render: renderMarkdown,
    async loadChannels() {
      try {
        this.channels = await (await fetch("/api/channels")).json();
      } catch (_) {
        this.channels = [];
      }
    },
    async run() {
      this.error = "";
      this.running = true;
      this.sections = [];
      try {
        const response = await fetch("/api/summarize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            channel_id: this.channelId,
            summary_types: this.types,
            force: this.force,
          }),
        });
        if (!response.ok) {
          this.error = (await response.json()).detail || "Failed.";
          return;
        }
        this.sections = (await response.json()).sections;
      } catch (_) {
        this.error = "Could not reach the server.";
      } finally {
        this.running = false;
      }
    },
    downloadSection(section) {
      downloadText(section.content, `${this.channelId}-${section.summary_type}.md`, "text/markdown");
    },
  };
}

function csvCell(value) {
  return `"${(value == null ? "" : String(value)).replace(/"/g, '""')}"`;
}

function quotesToCsv(rows) {
  const header = ["quote", "type", "video_title", "start_seconds", "url", "speaker", "context"];
  const lines = [header.join(",")];
  for (const q of rows) {
    lines.push(
      [q.text, q.quote_type, q.video_title, q.start_seconds, q.url, q.speaker_guess, q.context]
        .map(csvCell)
        .join(",")
    );
  }
  return lines.join("\n");
}

function quotesToMarkdown(rows) {
  const lines = ["# Quotes", ""];
  for (const q of rows) {
    lines.push(`> ${q.text}`);
    let attribution = `— *${q.quote_type}*, [${q.video_title}](${q.url})`;
    if (q.speaker_guess) attribution += ` · ${q.speaker_guess}`;
    lines.push(attribution, "");
  }
  return `${lines.join("\n").trim()}\n`;
}

function quotesPanel() {
  return {
    channels: [],
    videos: [],
    source: "channel",
    id: "",
    allTypes: ["statement", "prediction", "stat", "claim", "list"],
    types: [],
    regenerate: false,
    running: false,
    error: "",
    quotes: [],
    filterType: "",
    clock,
    async loadSources() {
      try {
        this.channels = await (await fetch("/api/channels")).json();
        this.videos = await (await fetch("/api/videos")).json();
      } catch (_) {
        /* leave empty on failure */
      }
    },
    onSourceChange() {
      this.id = "";
    },
    filtered() {
      return this.filterType
        ? this.quotes.filter((q) => q.quote_type === this.filterType)
        : this.quotes;
    },
    async run() {
      if (!this.id) return;
      this.error = "";
      this.running = true;
      this.quotes = [];
      try {
        const response = await fetch("/api/quotes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source: this.source,
            id: this.id,
            quote_types: this.types,
            regenerate: this.regenerate,
          }),
        });
        if (!response.ok) {
          this.error = (await response.json()).detail || "Failed.";
          return;
        }
        this.quotes = (await response.json()).quotes;
        if (!this.quotes.length) this.error = "No quotes found.";
      } catch (_) {
        this.error = "Could not reach the server.";
      } finally {
        this.running = false;
      }
    },
    download(fmt) {
      const rows = this.filtered();
      if (fmt === "json") {
        downloadText(JSON.stringify(rows, null, 2), "quotes.json", "application/json");
      } else if (fmt === "csv") {
        downloadText(quotesToCsv(rows), "quotes.csv", "text/csv");
      } else {
        downloadText(quotesToMarkdown(rows), "quotes.md", "text/markdown");
      }
    },
  };
}
