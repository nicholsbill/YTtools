# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""SQLite access layer.

Every database operation in YTtools goes through the :class:`Database` class.
A single connection is opened with WAL journaling and foreign keys enforced,
guarded by a lock so it can be shared safely across worker threads. Async
callers wrap calls in :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import importlib.resources
import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any

from yttools.core.models import (
    Channel,
    Chapter,
    Job,
    JobStatus,
    Playlist,
    Quote,
    Segment,
    Summary,
    Topic,
    Transcript,
    Video,
)

logger = logging.getLogger(__name__)

_MIGRATIONS_PACKAGE = "yttools.core.migrations"

# The sqlite-vec virtual table for chunk embeddings. Created only when the
# extension loads; the Ask tool (v0.3.0) is what populates it. Auxiliary columns
# use sqlite-vec's `float` type (its parser rejects the SQL `REAL` spelling).
_CHUNKS_TABLE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING vec0(
    embedding float[768],
    +video_id TEXT,
    +chunk_index INTEGER,
    +start_seconds float,
    +text TEXT
)
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _try_load_vec(conn: sqlite3.Connection) -> bool:
    """Attempt to load the sqlite-vec extension. Returns whether it succeeded."""
    try:
        conn.enable_load_extension(True)
    except (AttributeError, sqlite3.OperationalError, sqlite3.NotSupportedError):
        logger.debug("sqlite extension loading is unavailable in this build")
        return False
    try:
        import sqlite_vec

        sqlite_vec.load(conn)
        return True
    except Exception:
        # Any failure here simply means vector search stays disabled.
        logger.debug("sqlite-vec extension did not load; vector features disabled")
        return False
    finally:
        try:
            conn.enable_load_extension(False)
        except (AttributeError, sqlite3.OperationalError, sqlite3.NotSupportedError):
            pass


class Database:
    """Owns a single SQLite connection and exposes typed accessors."""

    def __init__(self, connection: sqlite3.Connection, *, vec_available: bool) -> None:
        self._conn = connection
        self._lock = threading.Lock()
        self.vec_available = vec_available

    @classmethod
    def open(cls, db_path: Path | str, *, run_migrations: bool = True) -> Database:
        path = Path(db_path)
        if path != Path(":memory:") and str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        vec_available = _try_load_vec(conn)
        database = cls(conn, vec_available=vec_available)
        if run_migrations:
            database.migrate()
        return database

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    # -- migrations ------------------------------------------------------

    def migrate(self) -> list[int]:
        """Apply any unapplied migrations. Returns the versions just applied."""
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            applied = {
                row["version"]
                for row in self._conn.execute("SELECT version FROM schema_migrations")
            }
            newly_applied: list[int] = []
            for version, sql in _load_migrations():
                if version in applied:
                    continue
                self._conn.executescript(sql)
                self._conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
                newly_applied.append(version)
            if self.vec_available:
                try:
                    self._conn.execute(_CHUNKS_TABLE_SQL)
                except sqlite3.OperationalError:
                    # A vec0 incompatibility must not break the rest of the schema.
                    self.vec_available = False
                    logger.debug("chunks vector table could not be created; disabling vec features")
            self._conn.commit()
            return newly_applied

    # -- channels --------------------------------------------------------

    def upsert_channel(self, channel: Channel) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO channels "
                "(id, handle, title, description, subscriber_count, video_count, last_refreshed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET handle=excluded.handle, title=excluded.title,"
                " description=excluded.description, subscriber_count=excluded.subscriber_count,"
                " video_count=excluded.video_count, last_refreshed_at=excluded.last_refreshed_at",
                (
                    channel.id,
                    channel.handle,
                    channel.title,
                    channel.description,
                    channel.subscriber_count,
                    channel.video_count,
                    _now(),
                ),
            )
            self._conn.commit()

    def get_channel(self, channel_id: str) -> Channel | None:
        row = self._fetchone("SELECT * FROM channels WHERE id = ?", (channel_id,))
        return _row_to_channel(row) if row else None

    def list_channels(self) -> list[Channel]:
        rows = self._fetchall("SELECT * FROM channels ORDER BY title COLLATE NOCASE")
        return [_row_to_channel(row) for row in rows]

    # -- playlists -------------------------------------------------------

    def upsert_playlist(self, playlist: Playlist) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO playlists "
                "(id, channel_id, title, description, video_count, last_refreshed_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET channel_id=excluded.channel_id,"
                " title=excluded.title, description=excluded.description,"
                " video_count=excluded.video_count, last_refreshed_at=excluded.last_refreshed_at",
                (
                    playlist.id,
                    playlist.channel_id,
                    playlist.title,
                    playlist.description,
                    playlist.video_count,
                    _now(),
                ),
            )
            self._conn.commit()

    def add_playlist_video(self, playlist_id: str, video_id: str, position: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO playlist_videos (playlist_id, video_id, position) VALUES (?, ?, ?)"
                " ON CONFLICT(playlist_id, video_id) DO UPDATE SET position=excluded.position",
                (playlist_id, video_id, position),
            )
            self._conn.commit()

    def list_playlists(self) -> list[Playlist]:
        rows = self._fetchall("SELECT * FROM playlists ORDER BY title COLLATE NOCASE")
        return [_row_to_playlist(row) for row in rows]

    # -- videos ----------------------------------------------------------

    def upsert_video(self, video: Video) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO videos (id, channel_id, title, description, published_at,"
                " duration_seconds, view_count, like_count, thumbnail_url, chapters_json,"
                " tags_json, last_refreshed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET channel_id=excluded.channel_id,"
                " title=excluded.title, description=excluded.description,"
                " published_at=excluded.published_at, duration_seconds=excluded.duration_seconds,"
                " view_count=excluded.view_count, like_count=excluded.like_count,"
                " thumbnail_url=excluded.thumbnail_url, chapters_json=excluded.chapters_json,"
                " tags_json=excluded.tags_json, last_refreshed_at=excluded.last_refreshed_at",
                (
                    video.id,
                    video.channel_id,
                    video.title,
                    video.description,
                    video.published_at.isoformat() if video.published_at else None,
                    video.duration_seconds,
                    video.view_count,
                    video.like_count,
                    video.thumbnail_url,
                    json.dumps([chapter.model_dump() for chapter in video.chapters]),
                    json.dumps(video.tags),
                    _now(),
                ),
            )
            self._conn.commit()

    def get_video(self, video_id: str) -> Video | None:
        row = self._fetchone("SELECT * FROM videos WHERE id = ?", (video_id,))
        return _row_to_video(row) if row else None

    def list_videos(self, channel_id: str | None = None) -> list[Video]:
        if channel_id:
            rows = self._fetchall(
                "SELECT * FROM videos WHERE channel_id = ? ORDER BY published_at DESC",
                (channel_id,),
            )
        else:
            rows = self._fetchall("SELECT * FROM videos ORDER BY published_at DESC")
        return [_row_to_video(row) for row in rows]

    def video_exists(self, video_id: str) -> bool:
        return self._fetchone("SELECT 1 FROM videos WHERE id = ?", (video_id,)) is not None

    def count_videos(self) -> int:
        row = self._fetchone("SELECT COUNT(*) AS n FROM videos")
        return int(row["n"]) if row else 0

    def last_fetch_time(self) -> datetime | None:
        row = self._fetchone("SELECT MAX(last_refreshed_at) AS t FROM videos")
        return _parse_dt(row["t"]) if row else None

    # -- transcripts -----------------------------------------------------

    def upsert_transcript(self, transcript: Transcript) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO transcripts "
                "(video_id, language, is_auto_generated, text, segments_json, word_count)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(video_id) DO UPDATE SET language=excluded.language,"
                " is_auto_generated=excluded.is_auto_generated, text=excluded.text,"
                " segments_json=excluded.segments_json, word_count=excluded.word_count,"
                " fetched_at=CURRENT_TIMESTAMP",
                (
                    transcript.video_id,
                    transcript.language,
                    int(transcript.is_auto_generated),
                    transcript.text,
                    json.dumps([segment.model_dump() for segment in transcript.segments]),
                    transcript.word_count,
                ),
            )
            self._conn.commit()

    def get_transcript(self, video_id: str) -> Transcript | None:
        row = self._fetchone("SELECT * FROM transcripts WHERE video_id = ?", (video_id,))
        return _row_to_transcript(row) if row else None

    def transcript_exists(self, video_id: str) -> bool:
        row = self._fetchone("SELECT 1 FROM transcripts WHERE video_id = ?", (video_id,))
        return row is not None

    def video_needs_fetch(
        self, video_id: str, *, force_refresh: bool, max_age_days: int = 7
    ) -> bool:
        """Decide whether a video should be (re)fetched.

        A video is skipped only when it already exists, has a transcript, and was
        refreshed within ``max_age_days``. ``force_refresh`` overrides all of that.
        """
        if force_refresh:
            return True
        row = self._fetchone(
            "SELECT v.last_refreshed_at AS refreshed, t.video_id AS has_transcript"
            " FROM videos v LEFT JOIN transcripts t ON t.video_id = v.id WHERE v.id = ?",
            (video_id,),
        )
        if row is None or row["has_transcript"] is None:
            return True
        refreshed = _parse_dt(row["refreshed"])
        if refreshed is None:
            return True
        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
        if refreshed.tzinfo is None:
            refreshed = refreshed.replace(tzinfo=UTC)
        return refreshed < cutoff

    # -- jobs ------------------------------------------------------------

    def create_job(self, job: Job) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, kind, status, input_json, progress_total)"
                " VALUES (?, ?, ?, ?, ?)",
                (job.id, job.kind, job.status, job.input_json, job.progress_total),
            )
            self._conn.commit()

    def update_job(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        output_json: str | None = None,
        error_message: str | None = None,
        mark_started: bool = False,
        mark_finished: bool = False,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if progress_current is not None:
            sets.append("progress_current = ?")
            params.append(progress_current)
        if progress_total is not None:
            sets.append("progress_total = ?")
            params.append(progress_total)
        if output_json is not None:
            sets.append("output_json = ?")
            params.append(output_json)
        if error_message is not None:
            sets.append("error_message = ?")
            params.append(error_message)
        if mark_started:
            sets.append("started_at = ?")
            params.append(_now())
        if mark_finished:
            sets.append("finished_at = ?")
            params.append(_now())
        if not sets:
            return
        params.append(job_id)
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)
            self._conn.commit()

    def get_job(self, job_id: str) -> Job | None:
        row = self._fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return _row_to_job(row) if row else None

    def count_active_jobs(self) -> int:
        row = self._fetchone("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued', 'running')")
        return int(row["n"]) if row else 0

    # -- summaries -------------------------------------------------------

    def upsert_summary(self, summary: Summary) -> None:
        """Insert or replace a summary keyed by (target_type, target_id, summary_type)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO summaries (target_type, target_id, summary_type, content, model_used)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(target_type, target_id, summary_type) DO UPDATE SET"
                " content=excluded.content, model_used=excluded.model_used,"
                " generated_at=CURRENT_TIMESTAMP",
                (
                    summary.target_type,
                    summary.target_id,
                    summary.summary_type,
                    summary.content,
                    summary.model_used,
                ),
            )
            self._conn.commit()

    def get_summary(self, target_type: str, target_id: str, summary_type: str) -> Summary | None:
        row = self._fetchone(
            "SELECT * FROM summaries WHERE target_type=? AND target_id=? AND summary_type=?",
            (target_type, target_id, summary_type),
        )
        return _row_to_summary(row) if row else None

    # -- quotes ----------------------------------------------------------

    def delete_quotes_for_video(self, video_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM quotes WHERE video_id = ?", (video_id,))
            self._conn.commit()

    def add_quotes(self, quotes: list[Quote]) -> None:
        if not quotes:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO quotes (video_id, text, quote_type, start_seconds, end_seconds,"
                " context, speaker_guess, model_used) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        q.video_id,
                        q.text,
                        q.quote_type,
                        q.start_seconds,
                        q.end_seconds,
                        q.context,
                        q.speaker_guess,
                        q.model_used,
                    )
                    for q in quotes
                ],
            )
            self._conn.commit()

    def list_quotes(
        self,
        *,
        video_ids: list[str] | None = None,
        quote_types: list[str] | None = None,
    ) -> list[Quote]:
        clauses: list[str] = []
        params: list[Any] = []
        if video_ids:
            clauses.append(f"video_id IN ({', '.join('?' for _ in video_ids)})")
            params.extend(video_ids)
        if quote_types:
            clauses.append(f"quote_type IN ({', '.join('?' for _ in quote_types)})")
            params.extend(quote_types)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._fetchall(
            f"SELECT * FROM quotes{where} ORDER BY video_id, start_seconds", tuple(params)
        )
        return [_row_to_quote(row) for row in rows]

    # -- topics ----------------------------------------------------------

    def clear_topics(self, channel_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM video_topics WHERE topic_id IN"
                " (SELECT id FROM topics WHERE channel_id = ?)",
                (channel_id,),
            )
            self._conn.execute("DELETE FROM topics WHERE channel_id = ?", (channel_id,))
            self._conn.commit()

    def add_topic(self, topic: Topic) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO topics (channel_id, label, first_video_id, last_video_id,"
                " first_seen_at, last_seen_at, video_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    topic.channel_id,
                    topic.label,
                    topic.first_video_id,
                    topic.last_video_id,
                    topic.first_seen_at.isoformat() if topic.first_seen_at else None,
                    topic.last_seen_at.isoformat() if topic.last_seen_at else None,
                    topic.video_count,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def add_video_topic(self, video_id: str, topic_id: int, relevance: float = 1.0) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO video_topics (video_id, topic_id, relevance)"
                " VALUES (?, ?, ?)",
                (video_id, topic_id, relevance),
            )
            self._conn.commit()

    def list_topics(self, channel_id: str) -> list[Topic]:
        rows = self._fetchall(
            "SELECT * FROM topics WHERE channel_id = ? ORDER BY video_count DESC, label",
            (channel_id,),
        )
        return [_row_to_topic(row) for row in rows]

    def list_video_topics(self, channel_id: str) -> list[dict[str, Any]]:
        """Return joined (video_id, topic_id, label, relevance, published_at) rows."""
        rows = self._fetchall(
            "SELECT vt.video_id, vt.topic_id, vt.relevance, t.label, v.published_at"
            " FROM video_topics vt JOIN topics t ON t.id = vt.topic_id"
            " JOIN videos v ON v.id = vt.video_id WHERE t.channel_id = ?",
            (channel_id,),
        )
        return [dict(row) for row in rows]

    # -- chunk embeddings (Ask) -----------------------------------------

    def delete_chunks_for_video(self, video_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chunk_embeddings WHERE video_id = ?", (video_id,))
            self._conn.commit()

    def add_chunk_embeddings(
        self, rows: list[tuple[str, str | None, int, float, str, str]]
    ) -> None:
        """Insert chunk rows: (video_id, channel_id, chunk_index, start, text, embedding_json)."""
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO chunk_embeddings"
                " (video_id, channel_id, chunk_index, start_seconds, text, embedding)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()

    def video_is_indexed(self, video_id: str) -> bool:
        row = self._fetchone(
            "SELECT 1 FROM chunk_embeddings WHERE video_id = ? LIMIT 1", (video_id,)
        )
        return row is not None

    def count_chunk_embeddings(self, channel_ids: list[str] | None = None) -> int:
        if channel_ids:
            placeholders = ", ".join("?" for _ in channel_ids)
            row = self._fetchone(
                f"SELECT COUNT(*) AS n FROM chunk_embeddings WHERE channel_id IN ({placeholders})",
                tuple(channel_ids),
            )
        else:
            row = self._fetchone("SELECT COUNT(*) AS n FROM chunk_embeddings")
        return int(row["n"]) if row else 0

    def list_chunk_embeddings(self, channel_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Return chunk rows with parsed embeddings, joined to video title and date."""
        sql = (
            "SELECT ce.video_id, ce.start_seconds, ce.text, ce.embedding,"
            " v.title AS video_title, v.published_at"
            " FROM chunk_embeddings ce JOIN videos v ON v.id = ce.video_id"
        )
        params: tuple[Any, ...] = ()
        if channel_ids:
            placeholders = ", ".join("?" for _ in channel_ids)
            sql += f" WHERE ce.channel_id IN ({placeholders})"
            params = tuple(channel_ids)
        return [
            {
                "video_id": row["video_id"],
                "video_title": row["video_title"],
                "start_seconds": row["start_seconds"],
                "text": row["text"],
                "embedding": json.loads(row["embedding"]),
                "published_at": row["published_at"],
            }
            for row in self._fetchall(sql, params)
        ]

    # -- search ----------------------------------------------------------

    def search_fts(
        self,
        match_query: str,
        *,
        channel_ids: list[str] | None = None,
        published_after: str | None = None,
        published_before: str | None = None,
        min_duration_seconds: int | None = None,
        max_duration_seconds: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Run an FTS5 MATCH query with optional filters, ranked by BM25."""
        where, params = self._build_search_filters(
            channel_ids,
            published_after,
            published_before,
            min_duration_seconds,
            max_duration_seconds,
        )
        sql = (
            "SELECT f.video_id AS video_id, bm25(transcripts_fts) AS score,"
            " snippet(transcripts_fts, 1, char(2), char(3), ' … ', 12) AS snippet,"
            " v.title AS title, v.channel_id AS channel_id, v.published_at AS published_at,"
            " v.duration_seconds AS duration_seconds, c.title AS channel_title"
            " FROM transcripts_fts f JOIN videos v ON v.id = f.video_id"
            " LEFT JOIN channels c ON c.id = v.channel_id"
            " WHERE transcripts_fts MATCH ?" + where + " ORDER BY score LIMIT ? OFFSET ?"
        )
        all_params = [match_query, *params, limit, offset]
        return [dict(row) for row in self._fetchall(sql, tuple(all_params))]

    def count_search_fts(
        self,
        match_query: str,
        *,
        channel_ids: list[str] | None = None,
        published_after: str | None = None,
        published_before: str | None = None,
        min_duration_seconds: int | None = None,
        max_duration_seconds: int | None = None,
    ) -> int:
        where, params = self._build_search_filters(
            channel_ids,
            published_after,
            published_before,
            min_duration_seconds,
            max_duration_seconds,
        )
        sql = (
            "SELECT COUNT(*) AS n FROM transcripts_fts f JOIN videos v ON v.id = f.video_id"
            " WHERE transcripts_fts MATCH ?" + where
        )
        row = self._fetchone(sql, (match_query, *params))
        return int(row["n"]) if row else 0

    @staticmethod
    def _build_search_filters(
        channel_ids: list[str] | None,
        published_after: str | None,
        published_before: str | None,
        min_duration_seconds: int | None,
        max_duration_seconds: int | None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if channel_ids:
            placeholders = ", ".join("?" for _ in channel_ids)
            clauses.append(f"v.channel_id IN ({placeholders})")
            params.extend(channel_ids)
        if published_after:
            clauses.append("v.published_at >= ?")
            params.append(published_after)
        if published_before:
            clauses.append("v.published_at <= ?")
            params.append(published_before)
        if min_duration_seconds is not None:
            clauses.append("v.duration_seconds >= ?")
            params.append(min_duration_seconds)
        if max_duration_seconds is not None:
            clauses.append("v.duration_seconds <= ?")
            params.append(max_duration_seconds)
        where = (" AND " + " AND ".join(clauses)) if clauses else ""
        return where, params

    # -- low-level helpers ----------------------------------------------

    def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            row: sqlite3.Row | None = cursor.fetchone()
            return row

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            rows: list[sqlite3.Row] = cursor.fetchall()
            return rows


def _load_migrations() -> list[tuple[int, str]]:
    resources = importlib.resources.files(_MIGRATIONS_PACKAGE)
    migrations: list[tuple[int, str]] = []
    for entry in resources.iterdir():
        name = entry.name
        if not name.endswith(".sql"):
            continue
        version = int(name.split("_", 1)[0])
        migrations.append((version, entry.read_text(encoding="utf-8")))
    migrations.sort(key=lambda item: item[0])
    return migrations


def _row_to_channel(row: sqlite3.Row) -> Channel:
    return Channel(
        id=row["id"],
        handle=row["handle"],
        title=row["title"],
        description=row["description"],
        subscriber_count=row["subscriber_count"],
        video_count=row["video_count"],
        first_seen_at=_parse_dt(row["first_seen_at"]),
        last_refreshed_at=_parse_dt(row["last_refreshed_at"]),
    )


def _row_to_playlist(row: sqlite3.Row) -> Playlist:
    return Playlist(
        id=row["id"],
        channel_id=row["channel_id"],
        title=row["title"],
        description=row["description"],
        video_count=row["video_count"],
        first_seen_at=_parse_dt(row["first_seen_at"]),
        last_refreshed_at=_parse_dt(row["last_refreshed_at"]),
    )


def _row_to_video(row: sqlite3.Row) -> Video:
    chapters = [Chapter(**item) for item in json.loads(row["chapters_json"] or "[]")]
    tags = json.loads(row["tags_json"] or "[]")
    return Video(
        id=row["id"],
        channel_id=row["channel_id"],
        title=row["title"],
        description=row["description"],
        published_at=_parse_dt(row["published_at"]),
        duration_seconds=row["duration_seconds"],
        view_count=row["view_count"],
        like_count=row["like_count"],
        thumbnail_url=row["thumbnail_url"],
        chapters=chapters,
        tags=tags,
        first_seen_at=_parse_dt(row["first_seen_at"]),
        last_refreshed_at=_parse_dt(row["last_refreshed_at"]),
    )


def _row_to_transcript(row: sqlite3.Row) -> Transcript:
    segments = [Segment(**item) for item in json.loads(row["segments_json"] or "[]")]
    return Transcript(
        video_id=row["video_id"],
        language=row["language"],
        is_auto_generated=bool(row["is_auto_generated"]),
        text=row["text"],
        segments=segments,
        word_count=row["word_count"],
        fetched_at=_parse_dt(row["fetched_at"]),
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        input_json=row["input_json"],
        output_json=row["output_json"],
        error_message=row["error_message"],
        progress_current=row["progress_current"],
        progress_total=row["progress_total"],
        started_at=_parse_dt(row["started_at"]),
        finished_at=_parse_dt(row["finished_at"]),
        created_at=_parse_dt(row["created_at"]),
    )


def _row_to_summary(row: sqlite3.Row) -> Summary:
    return Summary(
        id=row["id"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        summary_type=row["summary_type"],
        content=row["content"],
        model_used=row["model_used"],
        generated_at=_parse_dt(row["generated_at"]),
    )


def _row_to_quote(row: sqlite3.Row) -> Quote:
    return Quote(
        id=row["id"],
        video_id=row["video_id"],
        text=row["text"],
        quote_type=row["quote_type"],
        start_seconds=row["start_seconds"],
        end_seconds=row["end_seconds"],
        context=row["context"],
        speaker_guess=row["speaker_guess"],
        model_used=row["model_used"],
        extracted_at=_parse_dt(row["extracted_at"]),
    )


def _row_to_topic(row: sqlite3.Row) -> Topic:
    return Topic(
        id=row["id"],
        channel_id=row["channel_id"],
        label=row["label"],
        first_video_id=row["first_video_id"],
        last_video_id=row["last_video_id"],
        first_seen_at=_parse_dt(row["first_seen_at"]),
        last_seen_at=_parse_dt(row["last_seen_at"]),
        video_count=row["video_count"],
    )
