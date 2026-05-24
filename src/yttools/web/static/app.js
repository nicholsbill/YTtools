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

function formatCount(value) {
  if (value == null) return "";
  if (value >= 1e6) return `${(value / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1).replace(/\.0$/, "")}K`;
  return String(value);
}

// A "1.2K views · 340 likes · 12 comments" line from a video/result object.
function statsText(item) {
  if (!item) return "";
  const parts = [];
  if (item.view_count != null) parts.push(`${formatCount(item.view_count)} views`);
  if (item.like_count != null) parts.push(`${formatCount(item.like_count)} likes`);
  if (item.comment_count != null) parts.push(`${formatCount(item.comment_count)} comments`);
  return parts.join(" · ");
}

// Render a job's cost estimate (or "" when there's nothing to show).
function formatCost(cost) {
  if (!cost) return "";
  if (cost.local) return "local model · no API cost";
  const io = `${formatCount(cost.input_tokens)} in / ${formatCount(cost.output_tokens)} out`;
  if (cost.usd == null) return `${io} · price unknown for ${cost.model || cost.provider}`;
  const usd = cost.usd < 0.1 ? `$${cost.usd.toFixed(4)}` : `$${cost.usd.toFixed(2)}`;
  return `~${usd} · ${io}${cost.provider ? ` · ${cost.provider}` : ""}`;
}

// Convert the markdown-style **bold** match markers into highlighted spans.
// SECURITY: the snippet is escaped FIRST, so the only live markup added is our
// own <mark> tag — keep the escape before any markup when editing (x-html sink).
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
    statsText,
    render: renderSnippet,
    async loadChannels() {
      try {
        this.channels = await (await fetch("/api/channels")).json();
      } catch (_) {
        this.channels = [];
      }
    },
    highlight(text) {
      // SECURITY: transcript text is escaped FIRST (it feeds an x-html sink);
      // only our own <mark> tags are added afterwards. Keep this ordering.
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

// Render model-generated Markdown to HTML. The content is influenced by
// attacker-controllable transcripts, so the HTML is always sanitized with
// DOMPurify before it reaches x-html. If either library is missing (e.g. the
// CDN is blocked offline) we fall back to escaped plain text, never raw HTML.
function renderMarkdown(md) {
  const haveMarked = window.marked && typeof window.marked.parse === "function";
  const havePurify = window.DOMPurify && typeof window.DOMPurify.sanitize === "function";
  if (haveMarked && havePurify) {
    return window.DOMPurify.sanitize(window.marked.parse(md));
  }
  return `<pre class="whitespace-pre-wrap">${escapeHtml(md)}</pre>`;
}

function blogPanel() {
  return {
    videos: [],
    videoId: "",
    style: "",
    length: "medium",
    title: "",
    running: false,
    error: "",
    progress: "",
    jobId: null,
    cost: null,
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
      await resumeInto(this, "blog", (r) => this.apply(r));
    },
    apply(result) {
      this.markdown = result.markdown;
      this.rendered = renderMarkdown(result.markdown);
      this.modelUsed = result.model_used || "";
      this.wordCount = result.word_count || 0;
    },
    async convert() {
      if (!this.videoId) return;
      this.markdown = "";
      this.rendered = "";
      this.wordCount = 0;
      await runInto(
        this,
        "blog",
        "/api/blog",
        { video_id: this.videoId, style: this.style, length: this.length, title: this.title },
        (r) => this.apply(r)
      );
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

// --- background AI-tool jobs -------------------------------------------
// AI tools run server-side as background jobs that keep going if you navigate
// away. We poll /api/jobs/{id} for live progress and the final result, and
// remember the active job id per tool so returning to the tab reconnects.

function jobKey(tool) {
  return `yttools-job-${tool}`;
}

function formatProgress(progress) {
  if (!progress) return "";
  const { message, current, total } = progress;
  return total ? `${message} (${current}/${total})` : message || "";
}

// Poll a job to completion. Resolves with the result on "done", resolves with
// null on "cancelled", rejects on "error" or if the job disappears.
function pollJob(tool, jobId, onProgress) {
  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      let entry;
      try {
        const response = await fetch(`/api/jobs/${jobId}`);
        if (!response.ok) {
          clearInterval(timer);
          localStorage.removeItem(jobKey(tool));
          reject(new Error("The job is no longer available."));
          return;
        }
        entry = await response.json();
      } catch (_) {
        return; // transient; try again on the next tick
      }
      if (onProgress) onProgress(entry.progress);
      if (entry.status === "running") return;
      clearInterval(timer);
      localStorage.removeItem(jobKey(tool));
      if (entry.status === "done") resolve(entry.result);
      else if (entry.status === "cancelled") resolve(null);
      else reject(new Error(entry.detail || "The job failed."));
    }, 800);
  });
}

async function beginJob(tool, startUrl, body) {
  const response = await fetch(startUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error((await response.json()).detail || "Could not start the job.");
  const { job_id: jobId } = await response.json();
  localStorage.setItem(jobKey(tool), jobId);
  return jobId;
}

async function cancelJob(jobId) {
  if (!jobId) return;
  try {
    await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  } catch (_) {
    /* the poll will reflect the final state */
  }
}

// Drive a panel's running/progress/error/jobId state through a job, applying
// the result on success. `self` is the Alpine component; `apply(result)`
// renders it. A null result means the job was cancelled.
async function runInto(self, tool, url, body, apply) {
  self.error = "";
  self.running = true;
  self.progress = "Starting";
  try {
    self.jobId = await beginJob(tool, url, body);
    const result = await pollJob(tool, self.jobId, (p) => (self.progress = formatProgress(p)));
    if (result) {
      apply(result);
      self.cost = result.cost || null;
    }
  } catch (e) {
    self.error = e.message;
  } finally {
    self.running = false;
    self.jobId = null;
  }
}

async function resumeInto(self, tool, apply) {
  const jobId = localStorage.getItem(jobKey(tool));
  if (!jobId) return;
  try {
    const response = await fetch(`/api/jobs/${jobId}`); // a restart clears the registry
    if (!response.ok) {
      localStorage.removeItem(jobKey(tool));
      return;
    }
  } catch (_) {
    return;
  }
  self.error = "";
  self.running = true;
  self.jobId = jobId;
  try {
    const result = await pollJob(tool, jobId, (p) => (self.progress = formatProgress(p)));
    if (result) {
      apply(result);
      self.cost = result.cost || null;
    }
  } catch (e) {
    self.error = e.message;
  } finally {
    self.running = false;
    self.jobId = null;
  }
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
    progress: "",
    jobId: null,
    cost: null,
    sections: [],
    render: renderMarkdown,
    async loadChannels() {
      try {
        this.channels = await (await fetch("/api/channels")).json();
      } catch (_) {
        this.channels = [];
      }
      await resumeInto(this, "summarize", (r) => (this.sections = r.sections));
    },
    async run() {
      if (!this.channelId || !this.types.length) return;
      this.sections = [];
      await runInto(
        this,
        "summarize",
        "/api/summarize",
        { channel_id: this.channelId, summary_types: this.types, force: this.force },
        (r) => (this.sections = r.sections)
      );
    },
    downloadSection(section) {
      downloadText(section.content, `${this.channelId}-${section.summary_type}.md`, "text/markdown");
    },
  };
}

function csvCell(value) {
  let text = value == null ? "" : String(value);
  // Neutralize spreadsheet formula injection (cells starting with = + - @ etc.).
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
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
    progress: "",
    jobId: null,
    cost: null,
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
      await resumeInto(this, "quotes", (r) => (this.quotes = r.quotes));
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
      this.quotes = [];
      await runInto(
        this,
        "quotes",
        "/api/quotes",
        { source: this.source, id: this.id, quote_types: this.types, regenerate: this.regenerate },
        (r) => {
          this.quotes = r.quotes;
          if (!this.quotes.length) this.error = "No quotes found.";
        }
      );
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

function comparePanel() {
  return {
    channels: [],
    channelIds: [],
    running: false,
    error: "",
    progress: "",
    jobId: null,
    cost: null,
    result: null,
    tab: "Topic overlap",
    tabs: ["Topic overlap", "Vocabulary", "Timing"],
    async loadChannels() {
      try {
        this.channels = await (await fetch("/api/channels")).json();
      } catch (_) {
        this.channels = [];
      }
      await resumeInto(this, "compare", (r) => {
        this.result = r;
        this.tab = "Topic overlap";
      });
    },
    titleOf(id) {
      const known = this.channels.find((c) => c.id === id);
      if (known) return known.title;
      const fromResult = this.result && this.result.channels.find((c) => c.id === id);
      return fromResult ? fromResult.title : id;
    },
    async run() {
      if (this.channelIds.length < 2) return;
      this.result = null;
      await runInto(this, "compare", "/api/compare", { channel_ids: this.channelIds }, (r) => {
        this.result = r;
        this.tab = "Topic overlap";
      });
    },
  };
}

const TIMELINE_PALETTE = [
  "#ef4444", "#3b82f6", "#10b981", "#f59e0b",
  "#8b5cf6", "#ec4899", "#14b8a6", "#f97316",
];

function timelinePanel() {
  let chart = null; // kept out of Alpine's reactive graph
  return {
    channels: [],
    channelId: "",
    mode: "auto",
    topicsText: "",
    running: false,
    error: "",
    progress: "",
    jobId: null,
    cost: null,
    stats: [],
    hasData: false,
    async loadChannels() {
      try {
        this.channels = await (await fetch("/api/channels")).json();
      } catch (_) {
        this.channels = [];
      }
      await resumeInto(this, "timeline", (r) => this.apply(r));
    },
    apply(data) {
      this.stats = data.stats;
      if (!data.months.length) {
        this.error = "No data for that selection.";
        this.hasData = false;
        return;
      }
      this.hasData = true;
      this.$nextTick(() => this.draw(data));
    },
    async run() {
      if (!this.channelId) return;
      this.hasData = false;
      const topics = this.topicsText.split(",").map((s) => s.trim()).filter(Boolean);
      await runInto(
        this,
        "timeline",
        "/api/timeline",
        { channel_id: this.channelId, mode: this.mode, topics },
        (r) => this.apply(r)
      );
    },
    draw(data) {
      if (!window.Chart) return;
      if (chart) chart.destroy();
      const datasets = data.series.map((s, i) => ({
        label: s.topic,
        data: s.counts,
        backgroundColor: TIMELINE_PALETTE[i % TIMELINE_PALETTE.length],
        borderColor: TIMELINE_PALETTE[i % TIMELINE_PALETTE.length],
        fill: true,
      }));
      chart = new Chart(this.$refs.chart, {
        type: "line",
        data: { labels: data.months, datasets },
        options: {
          responsive: true,
          scales: { y: { stacked: true, beginAtZero: true }, x: { stacked: true } },
          elements: { line: { tension: 0.3 } },
          plugins: { legend: { labels: { boxWidth: 10 } } },
        },
      });
    },
  };
}

function askPanel() {
  return {
    channels: [],
    channelId: "",
    indexedChunks: 0,
    indexing: false,
    asking: false,
    error: "",
    progress: "",
    jobId: null,
    cost: null,
    question: "",
    answer: "",
    rendered: "",
    citations: [],
    steps: [],
    clock,
    async loadChannels() {
      try {
        this.channels = await (await fetch("/api/channels")).json();
      } catch (_) {
        this.channels = [];
      }
      await this.refreshStatus();
      // Reconnect to an indexing job still running after navigating away.
      const indexJob = localStorage.getItem(jobKey("ask-index"));
      if (indexJob) {
        this.indexing = true;
        this.jobId = indexJob;
        try {
          await pollJob("ask-index", indexJob, (p) => (this.progress = formatProgress(p)));
          await this.refreshStatus();
        } catch (e) {
          this.error = e.message;
        } finally {
          this.indexing = false;
          this.jobId = null;
        }
      }
    },
    async refreshStatus() {
      try {
        const q = this.channelId ? `?channel=${encodeURIComponent(this.channelId)}` : "";
        this.indexedChunks = (await (await fetch(`/api/ask/status${q}`)).json()).indexed_chunks;
      } catch (_) {
        this.indexedChunks = 0;
      }
    },
    async index() {
      if (!this.channelId) return;
      this.error = "";
      this.indexing = true;
      this.progress = "Starting";
      try {
        this.jobId = await beginJob("ask-index", "/api/ask/index", { channel_id: this.channelId });
        await pollJob("ask-index", this.jobId, (p) => (this.progress = formatProgress(p)));
        await this.refreshStatus();
      } catch (e) {
        this.error = e.message;
      } finally {
        this.indexing = false;
        this.jobId = null;
      }
    },
    async ask() {
      if (!this.question) return;
      this.error = "";
      this.asking = true;
      this.progress = "Starting";
      this.answer = "";
      this.citations = [];
      this.steps = [];
      try {
        const body = { question: this.question };
        if (this.channelId) body.channel_ids = [this.channelId];
        this.jobId = await beginJob("ask", "/api/ask", body);
        const data = await pollJob("ask", this.jobId, (p) => (this.progress = formatProgress(p)));
        if (data) {
          this.answer = data.answer;
          this.rendered = renderMarkdown(data.answer);
          this.citations = data.citations;
          this.steps = data.steps || [];
          this.cost = data.cost || null;
        }
      } catch (e) {
        this.error = e.message;
      } finally {
        this.asking = false;
        this.jobId = null;
      }
    },
  };
}
