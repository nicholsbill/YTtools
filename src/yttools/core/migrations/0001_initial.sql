-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2025 William Nichols and YTtools contributors
-- Initial schema: channels, playlists, videos, transcripts (+ FTS), quotes,
-- summaries, topics, jobs. The sqlite-vec `chunks` table is created separately
-- in db.py because it depends on a loadable extension.

CREATE TABLE channels (
    id                  TEXT PRIMARY KEY,
    handle              TEXT,
    title               TEXT NOT NULL,
    description         TEXT,
    subscriber_count    INTEGER,
    video_count         INTEGER,
    first_seen_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_refreshed_at   TIMESTAMP
);

CREATE TABLE playlists (
    id                  TEXT PRIMARY KEY,
    channel_id          TEXT,
    title               TEXT NOT NULL,
    description         TEXT,
    video_count         INTEGER,
    first_seen_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_refreshed_at   TIMESTAMP,
    FOREIGN KEY (channel_id) REFERENCES channels(id)
);

CREATE TABLE videos (
    id                  TEXT PRIMARY KEY,
    channel_id          TEXT,
    title               TEXT NOT NULL,
    description         TEXT,
    published_at        TIMESTAMP,
    duration_seconds    INTEGER,
    view_count          INTEGER,
    like_count          INTEGER,
    thumbnail_url       TEXT,
    chapters_json       TEXT,
    tags_json           TEXT,
    first_seen_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_refreshed_at   TIMESTAMP,
    FOREIGN KEY (channel_id) REFERENCES channels(id)
);

CREATE TABLE playlist_videos (
    playlist_id         TEXT,
    video_id            TEXT,
    position            INTEGER,
    PRIMARY KEY (playlist_id, video_id),
    FOREIGN KEY (playlist_id) REFERENCES playlists(id),
    FOREIGN KEY (video_id) REFERENCES videos(id)
);

CREATE TABLE transcripts (
    video_id            TEXT PRIMARY KEY,
    language            TEXT NOT NULL,
    is_auto_generated   BOOLEAN NOT NULL,
    text                TEXT NOT NULL,
    segments_json       TEXT NOT NULL,
    word_count          INTEGER,
    fetched_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (video_id) REFERENCES videos(id)
);

CREATE VIRTUAL TABLE transcripts_fts USING fts5(
    video_id UNINDEXED,
    text,
    tokenize='porter unicode61'
);

CREATE TRIGGER transcripts_ai AFTER INSERT ON transcripts BEGIN
    INSERT INTO transcripts_fts(video_id, text) VALUES (new.video_id, new.text);
END;

CREATE TRIGGER transcripts_au AFTER UPDATE ON transcripts BEGIN
    DELETE FROM transcripts_fts WHERE video_id = old.video_id;
    INSERT INTO transcripts_fts(video_id, text) VALUES (new.video_id, new.text);
END;

CREATE TRIGGER transcripts_ad AFTER DELETE ON transcripts BEGIN
    DELETE FROM transcripts_fts WHERE video_id = old.video_id;
END;

CREATE TABLE quotes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id            TEXT NOT NULL,
    text                TEXT NOT NULL,
    quote_type          TEXT NOT NULL,
    start_seconds       REAL,
    end_seconds         REAL,
    context             TEXT,
    speaker_guess       TEXT,
    model_used          TEXT,
    extracted_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (video_id) REFERENCES videos(id)
);

CREATE TABLE summaries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type         TEXT NOT NULL,
    target_id           TEXT NOT NULL,
    summary_type        TEXT NOT NULL,
    content             TEXT NOT NULL,
    model_used          TEXT,
    generated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (target_type, target_id, summary_type)
);

CREATE TABLE topics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id          TEXT NOT NULL,
    label               TEXT NOT NULL,
    first_video_id      TEXT,
    last_video_id       TEXT,
    first_seen_at       TIMESTAMP,
    last_seen_at        TIMESTAMP,
    video_count         INTEGER DEFAULT 0,
    FOREIGN KEY (channel_id) REFERENCES channels(id)
);

CREATE TABLE video_topics (
    video_id            TEXT,
    topic_id            INTEGER,
    relevance           REAL,
    PRIMARY KEY (video_id, topic_id),
    FOREIGN KEY (video_id) REFERENCES videos(id),
    FOREIGN KEY (topic_id) REFERENCES topics(id)
);

CREATE TABLE jobs (
    id                  TEXT PRIMARY KEY,
    kind                TEXT NOT NULL,
    status              TEXT NOT NULL,
    input_json          TEXT,
    output_json         TEXT,
    error_message       TEXT,
    progress_current    INTEGER DEFAULT 0,
    progress_total      INTEGER DEFAULT 0,
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_videos_channel ON videos(channel_id);
CREATE INDEX idx_videos_published ON videos(published_at);
CREATE INDEX idx_quotes_video ON quotes(video_id);
CREATE INDEX idx_quotes_type ON quotes(quote_type);
CREATE INDEX idx_topics_channel ON topics(channel_id);
CREATE INDEX idx_jobs_status ON jobs(status);
