-- Check-screen marks: moments in a video where the transcript alone is
-- insufficient. Populated from two sources: (a) the AI analysis, which
-- nominates candidates with kind='codex_suggested', and (b) the user,
-- who can freely mark segments with kind='user_marked', or accept an
-- AI suggestion (→ 'user_confirmed') or dismiss it (→ 'dismissed').
--
-- Unique(video_id, t_sec, analysis_id) prevents duplicate auto-ingestion
-- on re-runs. Note: for user-created marks, analysis_id is NULL, and
-- SQLite treats NULLs as distinct in UNIQUE indexes — so a user can
-- create multiple user_marked rows for the same t_sec without collision.

CREATE TABLE IF NOT EXISTS check_screen_marks (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id             TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    t_sec                REAL NOT NULL,
    segment_id           INTEGER,
    kind                 TEXT NOT NULL CHECK (kind IN ('codex_suggested','user_marked','user_confirmed','dismissed')),
    trigger_text         TEXT,
    signal               TEXT,
    what_i_expect_to_see TEXT,
    priority             TEXT CHECK (priority IN ('high','medium','low') OR priority IS NULL),
    note                 TEXT,
    analysis_id          INTEGER REFERENCES analyses(id) ON DELETE SET NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    UNIQUE (video_id, t_sec, analysis_id)
);
CREATE INDEX IF NOT EXISTS idx_check_marks_video    ON check_screen_marks(video_id);
CREATE INDEX IF NOT EXISTS idx_check_marks_analysis ON check_screen_marks(analysis_id);
