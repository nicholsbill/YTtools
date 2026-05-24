-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2025 William Nichols and YTtools contributors
-- Ask tool: a portable store for transcript chunk embeddings. This does not
-- depend on the sqlite-vec extension (the vec0 `chunks` table is created in
-- db.py only when the extension loads); embeddings are kept as JSON arrays and
-- searched by brute-force cosine, which is plenty for a local single-user app.

CREATE TABLE chunk_embeddings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        TEXT NOT NULL,
    channel_id      TEXT,
    chunk_index     INTEGER NOT NULL,
    start_seconds   REAL,
    text            TEXT NOT NULL,
    embedding       TEXT NOT NULL,
    FOREIGN KEY (video_id) REFERENCES videos(id)
);

CREATE INDEX idx_chunk_embeddings_video ON chunk_embeddings(video_id);
CREATE INDEX idx_chunk_embeddings_channel ON chunk_embeddings(channel_id);
