-- Folder-per-project layout.
--
-- Schema additions only; the data migration that backfills `project_id`
-- and `path` and physically moves video folders is in
-- `scripts/migrate_to_folder_per_project.py`. The `project_videos`
-- table is dropped in migration 005 once the script has run on every
-- existing DB. The script itself runs migration 005 directly so an
-- existing deployment doesn't have to wait for a server restart.
--
-- Once both migrations + the data migration have run, every videos row
-- has non-null project_id and path. SQLite can't add NOT NULL
-- retrospectively without a table rebuild, so the constraint is
-- enforced in application code (the layout helper raises if it ever
-- sees a NULL).

-- Single FK from videos to projects. Replaces project_videos (m:m).
ALTER TABLE videos ADD COLUMN project_id TEXT REFERENCES projects(id);

-- Absolute path to the per-video folder on disk. Today every video is
-- at OUTPUT_DIR / video_id; after the data migration, every video is
-- at OUTPUT_DIR / projects / <project_id> / <video_id>.
ALTER TABLE videos ADD COLUMN path TEXT;

-- Move-protocol latch. NULL when at rest; 'moving' between the start of
-- a move and its commit. Boot-time recovery scans this column.
ALTER TABLE videos ADD COLUMN move_state TEXT;

-- Inbox detection without a name match. NULL for user projects;
-- 'inbox' for the (one) system Inbox project that catches unprojected
-- videos and refuses deletion. Other system_kind values are reserved.
ALTER TABLE projects ADD COLUMN system_kind TEXT;

-- Per-project graph state for the graphify integration. `graph_state`
-- is the source of truth for the project page's "Knowledge Graph"
-- section.
ALTER TABLE projects ADD COLUMN graph_state TEXT NOT NULL DEFAULT 'never';
ALTER TABLE projects ADD COLUMN graph_built_at TEXT;
ALTER TABLE projects ADD COLUMN graph_node_count INTEGER;
ALTER TABLE projects ADD COLUMN graph_edge_count INTEGER;
ALTER TABLE projects ADD COLUMN graph_last_error TEXT;

-- Counter incremented on every event that would invalidate the graph
-- (ingest done, video moved in/out, new analysis written, archive
-- toggle). Reset to 0 when a graph build commits successfully. Pure
-- information signal; never auto-triggers a build.
ALTER TABLE projects ADD COLUMN events_since_build INTEGER NOT NULL DEFAULT 0;

-- Audit log for moves. Persisted across restarts so crash recovery
-- can match in-flight moves to their pre-failure state.
CREATE TABLE IF NOT EXISTS move_log (
    id           INTEGER PRIMARY KEY,
    video_id     TEXT NOT NULL,
    from_project TEXT,           -- NULL only for the very first migration insert
    to_project   TEXT NOT NULL,
    from_path    TEXT NOT NULL,
    to_path      TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,  -- 'started' | 'verified' | 'committed' | 'rolled_back' | 'failed'
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_move_log_video    ON move_log(video_id);
CREATE INDEX IF NOT EXISTS idx_move_log_status   ON move_log(status);

-- Index on the new FK so list-videos-in-project remains O(log n) even
-- after the project_videos drop.
CREATE INDEX IF NOT EXISTS idx_videos_project ON videos(project_id);
