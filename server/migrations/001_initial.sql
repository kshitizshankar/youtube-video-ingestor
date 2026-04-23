CREATE TABLE IF NOT EXISTS schema_migrations (
    name       TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE videos (
    id                            TEXT PRIMARY KEY,
    url                           TEXT NOT NULL,
    title                         TEXT,
    channel                       TEXT,
    channel_id                    TEXT,
    channel_url                   TEXT,
    channel_follower_count        INTEGER,
    upload_date                   TEXT,
    duration_sec                  REAL,
    description                   TEXT,
    categories                    TEXT,
    yt_tags                       TEXT,
    view_count                    INTEGER,
    like_count                    INTEGER,
    comment_count                 INTEGER,
    language                      TEXT,
    language_probability          REAL,
    diarized                      INTEGER NOT NULL DEFAULT 0,
    speaker_count                 INTEGER,
    segment_count                 INTEGER,
    model                         TEXT,
    compute_type                  TEXT,
    batched                       INTEGER,
    batch_size                    INTEGER,
    transcription_elapsed_sec     REAL,
    transcription_realtime_factor REAL,
    storage_bytes                 INTEGER,
    archived                      INTEGER NOT NULL DEFAULT 0,
    notes                         TEXT,
    owner                         TEXT,
    transcribed_at                TEXT,
    created_at                    TEXT NOT NULL,
    updated_at                    TEXT NOT NULL
);
CREATE INDEX idx_videos_created_at ON videos(created_at);
CREATE INDEX idx_videos_archived   ON videos(archived) WHERE archived = 1;

CREATE TABLE video_tags (
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    tag      TEXT NOT NULL,
    PRIMARY KEY (video_id, tag)
);

CREATE TABLE video_speakers (
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    label    TEXT NOT NULL,
    name     TEXT NOT NULL,
    PRIMARY KEY (video_id, label)
);

CREATE TABLE projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    owner       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE project_videos (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    video_id   TEXT NOT NULL REFERENCES videos(id)   ON DELETE CASCADE,
    added_at   TEXT NOT NULL,
    PRIMARY KEY (project_id, video_id)
);
CREATE INDEX idx_pv_project ON project_videos(project_id);
CREATE INDEX idx_pv_video   ON project_videos(video_id);

CREATE TABLE ingests (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id         TEXT NOT NULL,
    url              TEXT,
    title            TEXT,
    duration_sec     REAL,
    phase            TEXT NOT NULL,
    started_at       REAL NOT NULL,
    last_event_at    REAL NOT NULL,
    segments         INTEGER NOT NULL DEFAULT 0,
    last_segment_end REAL NOT NULL DEFAULT 0,
    done             INTEGER NOT NULL DEFAULT 0,
    error            TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    project_id       TEXT REFERENCES projects(id) ON DELETE SET NULL
);
CREATE INDEX idx_ingests_video  ON ingests(video_id);
CREATE INDEX idx_ingests_active ON ingests(video_id) WHERE done = 0;

CREATE TABLE analyses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    provider    TEXT NOT NULL,
    model       TEXT,
    status      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    cost_usd    REAL,
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    error       TEXT,
    file_path   TEXT
);
CREATE INDEX idx_analyses_video ON analyses(video_id);
