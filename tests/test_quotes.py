# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the Quotes tool. The LLM provider is faked; no network access."""

from __future__ import annotations

from yttools.core.db import Database
from yttools.core.models import Segment, Transcript, Video
from yttools.tools.quotes import (
    QuoteOut,
    QuotesResult,
    export_quotes,
    extract_quotes,
    load_quotes,
)

_PAYLOAD = (
    '{"quotes": [{"text": "AI will change everything", "type": "prediction", '
    '"speaker_guess": "Host", "context": "intro", "start_seconds": 12}]}'
)


class _FakeProvider:
    name = "fake"
    default_model = "fake-1"

    def __init__(self, payload: str) -> None:
        self._payload = payload
        self.calls = 0

    async def complete(self, prompt: str, **kwargs: object) -> str:
        self.calls += 1
        return self._payload


def _seed(db: Database) -> None:
    db.upsert_video(Video(id="vid1", title="A Talk", duration_seconds=600))
    db.upsert_transcript(
        Transcript(
            video_id="vid1",
            language="en",
            is_auto_generated=True,
            text="AI will change everything someday and the future is bright",
            segments=[Segment(start=12.0, end=18.0, text="AI will change everything someday")],
        )
    )


async def test_extract_persists_and_links(db: Database) -> None:
    _seed(db)
    result = await extract_quotes(db, _FakeProvider(_PAYLOAD), video_ids=["vid1"])
    assert result.total == 1
    quote = result.quotes[0]
    assert quote.quote_type == "prediction"
    assert "watch?v=vid1&t=12s" in quote.url
    assert db.list_quotes(video_ids=["vid1"])  # persisted


async def test_near_duplicates_are_merged(db: Database) -> None:
    _seed(db)
    payload = (
        '{"quotes": [{"text": "AI will change everything", "type": "prediction", '
        '"start_seconds": 12}, {"text": "AI will change everything!", "type": '
        '"prediction", "start_seconds": 13}]}'
    )
    result = await extract_quotes(db, _FakeProvider(payload), video_ids=["vid1"])
    assert result.total == 1


async def test_type_filter_restricts(db: Database) -> None:
    _seed(db)
    payload = (
        '{"quotes": [{"text": "Adoption hit 50 percent", "type": "stat", "start_seconds": 1}, '
        '{"text": "This is a strong claim", "type": "claim", "start_seconds": 2}]}'
    )
    result = await extract_quotes(
        db, _FakeProvider(payload), video_ids=["vid1"], quote_types=["stat"]
    )
    assert result.total == 1
    assert result.quotes[0].quote_type == "stat"


async def test_invalid_type_coerced_to_statement(db: Database) -> None:
    _seed(db)
    payload = '{"quotes": [{"text": "Something notable", "type": "bogus", "start_seconds": 1}]}'
    result = await extract_quotes(db, _FakeProvider(payload), video_ids=["vid1"])
    assert result.quotes[0].quote_type == "statement"


async def test_load_quotes_reads_persisted(db: Database) -> None:
    _seed(db)
    await extract_quotes(db, _FakeProvider(_PAYLOAD), video_ids=["vid1"])
    loaded = load_quotes(db, ["vid1"], None)
    assert loaded.total == 1
    assert loaded.quotes[0].video_title == "A Talk"


def test_export_formats() -> None:
    result = QuotesResult(
        total=1,
        quotes=[
            QuoteOut(
                video_id="v",
                video_title="T",
                text="Hi there",
                quote_type="statement",
                start_seconds=1.0,
                url="https://youtu.be/x",
                speaker_guess=None,
                context=None,
            )
        ],
    )
    csv_body, csv_type = export_quotes(result, "csv")
    assert "quote" in csv_body and "Hi there" in csv_body
    assert csv_type == "text/csv"
    json_body, _ = export_quotes(result, "json")
    assert '"text": "Hi there"' in json_body
    md_body, _ = export_quotes(result, "md")
    assert "> Hi there" in md_body


def test_csv_export_neutralizes_formula_injection() -> None:
    result = QuotesResult(
        total=1,
        quotes=[
            QuoteOut(
                video_id="v",
                video_title="=HYPERLINK('http://evil')",
                text="=1+1",
                quote_type="statement",
                start_seconds=0.0,
                url="https://youtu.be/x",
                speaker_guess="@handle",
                context="-2+3",
            )
        ],
    )
    csv_body, _ = export_quotes(result, "csv")
    # Formula-leading cells are prefixed with a single quote so spreadsheets
    # treat them as text rather than executing them.
    assert "'=1+1" in csv_body
    assert "'=HYPERLINK" in csv_body
    assert "'@handle" in csv_body
    assert "'-2+3" in csv_body
