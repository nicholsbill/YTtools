# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Ask agent: a JSON-driven tool loop over the local database.

The model is given a small catalog of read-only tools (search and count videos,
channel and per-video stats, compare videos, and transcript content search) and
asked to either call a tool or give a final answer, as a JSON object. We run the
tool against SQLite, feed the result back, and loop until it answers. Because the
figures come from the database rather than the model, counts and stats are
trustworthy. Works with any provider via JSON mode (no native tool-calling).
"""

from __future__ import annotations

import json
import re
from typing import Any

from yttools.core.db import Database
from yttools.core.exports import format_clock, watch_url
from yttools.core.llm import LLMError, LLMProvider
from yttools.core.progress import ProgressCallback, report
from yttools.tools.ask import AskError, AskResult, Citation, retrieve_chunks
from yttools.tools.search import SearchError, build_match_query

_MAX_STEPS = 6
_SEARCH_LIMIT = 15
_CONTENT_K = 6
_RESULT_CHARS = 6000
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class AgentError(RuntimeError):
    """Raised when the agent cannot produce an answer."""


def _snippet(text: str, limit: int = 160) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:limit] + ("…" if len(collapsed) > limit else "")


class _Toolbox:
    """Read-only data tools the agent can call. Each returns a JSON-able dict."""

    def __init__(self, database: Database, embed_provider: LLMProvider) -> None:
        self.db = database
        self.embed = embed_provider
        self.citations: list[Citation] = []
        self._channels: list[Any] | None = None

    def _channel_list(self) -> list[Any]:
        if self._channels is None:
            self._channels = self.db.list_channels()
        return self._channels

    def _resolve_channel(self, channel: Any) -> tuple[list[str] | None, str | None]:
        if not channel:
            return None, None
        wanted = str(channel).strip().lower()
        for c in self._channel_list():
            if c.id.lower() == wanted or wanted in c.title.lower():
                return [c.id], None
        available = [c.title for c in self._channel_list()]
        return None, f"unknown channel '{channel}'; available channels: {available}"

    def list_channels(self) -> dict[str, Any]:
        return {
            "channels": [
                {"id": c.id, "title": c.title, "video_count": len(self.db.list_videos(c.id))}
                for c in self._channel_list()
            ]
        }

    def _video_row(self, row: dict[str, Any]) -> dict[str, Any]:
        published = row.get("published_at")
        duration = row.get("duration_seconds")
        return {
            "video_id": row["video_id"],
            "title": row["title"],
            "channel": row.get("channel_title"),
            "views": row.get("view_count"),
            "likes": row.get("like_count"),
            "comments": row.get("comment_count"),
            "published": str(published)[:10] if published else None,
            "duration": format_clock(float(duration)) if duration else None,
            "url": watch_url(row["video_id"]),
        }

    def search_videos(
        self, query: Any, channel: Any = None, limit: Any = _SEARCH_LIMIT
    ) -> dict[str, Any]:
        channel_ids, error = self._resolve_channel(channel)
        if error:
            return {"error": error}
        try:
            match = build_match_query(str(query))
        except SearchError:
            return {"error": "empty or invalid query"}
        capped = max(1, min(int(limit or _SEARCH_LIMIT), 50))
        rows = self.db.search_fts(match, channel_ids=channel_ids, limit=capped)
        total = self.db.count_search_fts(match, channel_ids=channel_ids)
        return {
            "query": str(query),
            "channel": channel,
            "match_count": total,
            "videos": [self._video_row(row) for row in rows],
        }

    def channel_stats(self, channel: Any = None) -> dict[str, Any]:
        channel_ids, error = self._resolve_channel(channel)
        if error:
            return {"error": error}
        videos = self.db.list_videos(channel_ids[0] if channel_ids else None)
        if not videos:
            return {"channel": channel, "video_count": 0}
        views = [v.view_count for v in videos if v.view_count is not None]
        likes = [v.like_count for v in videos if v.like_count is not None]
        ranked = sorted(
            (v for v in videos if v.view_count is not None),
            key=lambda v: v.view_count or 0,
            reverse=True,
        )[:5]
        return {
            "channel": channel,
            "video_count": len(videos),
            "total_views": sum(views) if views else None,
            "average_views": round(sum(views) / len(views)) if views else None,
            "total_likes": sum(likes) if likes else None,
            "top_by_views": [
                {"title": v.title, "views": v.view_count, "url": watch_url(v.id)} for v in ranked
            ],
        }

    def compare_videos(self, video_ids: Any) -> dict[str, Any]:
        ids = video_ids if isinstance(video_ids, list) else []
        out: list[dict[str, Any]] = []
        for raw in ids[:5]:
            video = self.db.get_video(str(raw))
            if video is None:
                out.append({"video_id": raw, "error": "not found"})
                continue
            transcript = self.db.get_transcript(video.id)
            gist = " ".join(transcript.text.split()[:80]) if transcript else ""
            out.append(
                {
                    "video_id": video.id,
                    "title": video.title,
                    "views": video.view_count,
                    "likes": video.like_count,
                    "comments": video.comment_count,
                    "published": video.published_at.date().isoformat()
                    if video.published_at
                    else None,
                    "duration": format_clock(float(video.duration_seconds))
                    if video.duration_seconds
                    else None,
                    "url": watch_url(video.id),
                    "gist": gist,
                }
            )
        return {"videos": out}

    async def content_search(
        self, query: Any, channel: Any = None, k: Any = _CONTENT_K
    ) -> dict[str, Any]:
        channel_ids, error = self._resolve_channel(channel)
        if error:
            return {"error": error}
        try:
            top = await retrieve_chunks(
                self.db, self.embed, str(query), channel_ids, top_n=int(k or _CONTENT_K)
            )
        except AskError as exc:
            return {"error": str(exc)}
        passages: list[dict[str, Any]] = []
        for _score, row in top:
            start = float(row["start_seconds"] or 0.0)
            index = len(self.citations) + 1
            citation = Citation(
                index=index,
                video_id=str(row["video_id"]),
                title=str(row["video_title"]),
                start_seconds=start,
                url=watch_url(str(row["video_id"]), start),
                snippet=_snippet(str(row["text"])),
            )
            self.citations.append(citation)
            passages.append(
                {
                    "ref": index,
                    "video": citation.title,
                    "url": citation.url,
                    "text": str(row["text"])[:600],
                }
            )
        return {"passages": passages}

    async def run(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        safe = args if isinstance(args, dict) else {}
        try:
            if tool == "list_channels":
                return self.list_channels()
            if tool == "search_videos":
                return self.search_videos(
                    safe.get("query"), safe.get("channel"), safe.get("limit", _SEARCH_LIMIT)
                )
            if tool == "channel_stats":
                return self.channel_stats(safe.get("channel"))
            if tool == "compare_videos":
                return self.compare_videos(safe.get("video_ids"))
            if tool == "content_search":
                return await self.content_search(
                    safe.get("query"), safe.get("channel"), safe.get("k", _CONTENT_K)
                )
            return {"error": f"unknown tool '{tool}'"}
        except Exception as exc:  # keep one bad tool call from killing the run
            return {"error": str(exc)}


_SYSTEM = (
    "You are a data analyst for a local database of YouTube videos and their "
    "transcripts. Answer the user's question by calling tools to gather facts, "
    "then giving a final answer. Never invent numbers; every figure must come "
    "from a tool result.\n\n"
    "Respond with a single JSON object and nothing else. Either call a tool:\n"
    '  {"tool": "<name>", "args": {...}}\n'
    "or finish:\n"
    '  {"answer": "<concise markdown answer with the figures, linking videos where useful>"}\n\n'
    "Tools:\n"
    "- list_channels(): channels available, with id, title, and video_count.\n"
    "- search_videos(query, channel?, limit?): videos whose transcript matches the "
    "plain-word query; returns match_count and the matching videos with stats. "
    "channel may be a name or id.\n"
    "- channel_stats(channel?): video_count, total/average views, total likes, and "
    "top videos by views for a channel (or all channels if omitted).\n"
    "- compare_videos(video_ids): side-by-side stats and a transcript gist for the "
    "given video ids.\n"
    "- content_search(query, channel?): the most relevant transcript passages, each "
    "with a numbered reference and a timestamped link; use it for 'what did they "
    "say' questions and to cite sources with [n] markers.\n\n"
    "Guidelines: resolve channel names with list_channels if unsure; for counts use "
    "search_videos and report match_count per channel; you only have views, likes, "
    "comments, dates, durations, titles, and transcripts (no YouTube analytics like "
    "watch time or click-through), so when asked why a video performed differently, "
    "reason from those signals and say it is inferred."
)


def _parse_action(raw: str) -> dict[str, Any]:
    cleaned = _FENCE.sub("", raw.strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _describe(tool: str, args: dict[str, Any]) -> str:
    inner = ", ".join(f"{k}={v!r}" for k, v in args.items()) if isinstance(args, dict) else ""
    return f"{tool}({inner})"


def _build_prompt(
    question: str, channel_hint: str | None, scratchpad: list[dict[str, Any]], *, force: bool
) -> str:
    lines = [f"Question: {question}"]
    if channel_hint:
        lines.append(
            f"(The user is currently focused on: {channel_hint}. Search other "
            "channels too if the question calls for it.)"
        )
    if scratchpad:
        lines.append("\nWork so far:")
        for i, item in enumerate(scratchpad, start=1):
            if "note" in item:
                lines.append(f"{i}. {item['note']}")
                continue
            result = json.dumps(item["result"])[:_RESULT_CHARS]
            lines.append(f"{i}. called {item['tool']}({json.dumps(item['args'])}) -> {result}")
    if force:
        lines.append(
            "\nYou are out of tool calls. Answer now using the work above. "
            'Respond with {"answer": "..."} only.'
        )
    else:
        lines.append(
            '\nReply with JSON: {"tool": "<name>", "args": {...}} to gather more, '
            'or {"answer": "<markdown>"} to finish.'
        )
    return "\n".join(lines)


def _link_citations(answer: str, citations: list[Citation]) -> str:
    for citation in sorted(citations, key=lambda c: c.index, reverse=True):
        answer = answer.replace(f"[{citation.index}]", f"[[{citation.index}]]({citation.url})")
    return answer


async def _decide(provider: LLMProvider, prompt: str, model: str | None) -> dict[str, Any]:
    try:
        raw = await provider.complete(
            prompt,
            model=model,
            system=_SYSTEM,
            response_format="json",
            max_tokens=1500,
            temperature=0.1,
        )
    except LLMError as error:
        raise AgentError(str(error)) from error
    return _parse_action(raw)


async def run_agent(
    database: Database,
    answer_provider: LLMProvider,
    embed_provider: LLMProvider,
    question: str,
    *,
    channel_hint: str | None = None,
    model: str | None = None,
    max_steps: int = _MAX_STEPS,
    on_progress: ProgressCallback | None = None,
) -> AskResult:
    """Answer a question by letting the model drive tool calls over the database."""
    if not question.strip():
        raise AgentError("Ask a question")
    toolbox = _Toolbox(database, embed_provider)
    scratchpad: list[dict[str, Any]] = []
    steps: list[str] = []

    for step in range(1, max_steps + 1):
        await report(on_progress, f"Thinking (step {step})", step, max_steps)
        action = await _decide(
            answer_provider, _build_prompt(question, channel_hint, scratchpad, force=False), model
        )
        if action.get("answer"):
            answer = _link_citations(str(action["answer"]).strip(), toolbox.citations)
            return AskResult(answer=answer, citations=toolbox.citations, steps=steps)
        tool = action.get("tool")
        if not tool:
            scratchpad.append({"note": "Invalid response; reply with a tool call or an answer."})
            continue
        raw_args = action.get("args")
        args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        await report(on_progress, f"Running {tool}", step, max_steps)
        result = await toolbox.run(str(tool), args)
        steps.append(_describe(str(tool), args))
        scratchpad.append({"tool": tool, "args": args, "result": result})

    await report(on_progress, "Answering", max_steps, max_steps)
    action = await _decide(
        answer_provider, _build_prompt(question, channel_hint, scratchpad, force=True), model
    )
    answer = str(
        action.get("answer") or "I could not reach a confident answer from the available data."
    )
    return AskResult(
        answer=_link_citations(answer.strip(), toolbox.citations),
        citations=toolbox.citations,
        steps=steps,
    )
