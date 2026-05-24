-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2025 William Nichols and YTtools contributors
-- Store the video comment count alongside views and likes.

ALTER TABLE videos ADD COLUMN comment_count INTEGER;
