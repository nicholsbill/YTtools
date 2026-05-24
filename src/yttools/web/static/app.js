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
