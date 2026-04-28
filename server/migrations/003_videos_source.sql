-- Source-aware ingest: distinguish YouTube videos from podcast episodes.
--
-- `source` is the discriminator the UI branches on to pick the right
-- player (mp4/embed vs audio) and header layout (channel vs show).
-- `image_url` is an explicit thumbnail URL for podcasts (YouTube ingests
-- leave it NULL since the URL is derivable from the id).
-- `show_name` / `show_url` carry the podcast show + RSS feed; NULL for
-- YouTube videos.
--
-- All four columns are NULLable except `source`, which defaults to
-- 'youtube' so existing rows (all live YouTube videos at the time of
-- this migration) get the correct discriminator without a backfill.

ALTER TABLE videos ADD COLUMN source TEXT NOT NULL DEFAULT 'youtube';
ALTER TABLE videos ADD COLUMN image_url TEXT;
ALTER TABLE videos ADD COLUMN show_name TEXT;
ALTER TABLE videos ADD COLUMN show_url TEXT;
