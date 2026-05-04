-- Drop the m:m project_videos table now that videos.project_id is the
-- single source of truth.
--
-- Idempotent: a fresh DB runs this after 004 with the table empty (it
-- was just created in 001). On an existing deployment, the data
-- migration script (scripts/migrate_to_folder_per_project.py) backfills
-- videos.project_id from project_videos BEFORE invoking this drop, so
-- no membership data is lost.
--
-- Drop the indexes first to avoid SQLite leaving them as zombie refs
-- (older SQLite versions) -- they're cleaned up automatically with
-- DROP TABLE on modern SQLite, but explicit is safer.

DROP INDEX IF EXISTS idx_pv_project;
DROP INDEX IF EXISTS idx_pv_video;
DROP TABLE IF EXISTS project_videos;
