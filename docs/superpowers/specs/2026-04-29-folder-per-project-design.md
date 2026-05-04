# Folder-per-project + graphify integration

**Date:** 2026-04-29
**Status:** spec; awaiting user sign-off
**Touches:** every file-path helper in `server/`, the videos schema, the
ingest/queue/archive paths, the projects page in `web/`, plus a new
graphify subprocess wrapper.

---

## Motivation

graphify is a folder-in -> graph-out tool. Today video data is scattered:
`output/<video_id>/transcript.json`, with `project_videos` (DB) joining
videos to projects. Pointing graphify at a "project" requires assembling
a staging folder at build time, copying transcripts, and tracking dirty
state so updates know what to re-extract.

A simpler model: **every video lives inside its project folder on disk**.
graphify points at `output/projects/<project_id>/`; the corpus is already
there, organized exactly as graphify expects. New ingests, removals, and
re-analyses naturally update the corpus on disk. The trigger surface
shrinks to one button per project.

The cost is a one-shot data migration and a careful move protocol when
videos change project membership.

---

## Locked decisions

1. **Many-to-many is killed.** `videos.project_id` becomes a non-null FK.
   The `project_videos` join table is dropped. Currently 0 videos have
   multi-project membership, so this is a policy change without data
   loss.
2. **Inbox project.** A user-visible, non-deletable project named
   "Inbox". Every new ingest lands there unless the user picked a target
   project at submit time. Existing 23 unprojected videos migrate into
   it. Renameable; not deletable.
3. **Archive stays in-place.** `videos.archived = 1` remains a flag; the
   folder doesn't move on archive/restore. graphify build filters out
   `archived = 1` videos.
4. **Move protocol: copy -> verify -> DB swap -> delete source.** Source
   is preserved until the destination is verified. Crash recovery scans
   for in-flight moves at boot.
5. **Graphify trigger is fully manual.** A button on the project page
   kicks a build; no background workers. A "events since last build"
   counter informs the user without pre-empting their decision.

---

## Schema

### `videos` — new + modified columns

```sql
ALTER TABLE videos ADD COLUMN project_id TEXT REFERENCES projects(id);
ALTER TABLE videos ADD COLUMN path        TEXT;   -- absolute path to the per-video folder
ALTER TABLE videos ADD COLUMN move_state  TEXT;   -- NULL when at rest; 'moving' during transfer
-- After migration: project_id NOT NULL, path NOT NULL.
-- (SQLite can't add NOT NULL retrospectively without a table rebuild;
--  enforce in application code + a CHECK constraint via the rebuild.)
```

### `projects` — additive

```sql
ALTER TABLE projects ADD COLUMN system_kind  TEXT;   -- NULL for user projects;
                                                     -- 'inbox' for the Inbox project
ALTER TABLE projects ADD COLUMN graph_state  TEXT DEFAULT 'never';
                                                     -- 'never' | 'building' | 'ready' | 'error'
ALTER TABLE projects ADD COLUMN graph_built_at TEXT; -- ISO timestamp
ALTER TABLE projects ADD COLUMN graph_node_count INT;
ALTER TABLE projects ADD COLUMN graph_edge_count INT;
ALTER TABLE projects ADD COLUMN graph_last_error TEXT;
ALTER TABLE projects ADD COLUMN events_since_build INT NOT NULL DEFAULT 0;
```

### `move_log` — new table (audit)

```sql
CREATE TABLE move_log (
    id            INTEGER PRIMARY KEY,
    video_id      TEXT NOT NULL,
    from_project  TEXT,
    to_project    TEXT NOT NULL,
    from_path     TEXT NOT NULL,
    to_path       TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,   -- 'started' | 'verified' | 'committed' | 'rolled_back' | 'failed'
    error         TEXT
);
```

### `project_videos` — dropped after migration

The migration script reads it once to set `videos.project_id`, then
drops it.

---

## Filesystem layout

### Before

```
output/
  <video_id>/                 (flat, ~74 of these)
    transcript.json
    audio.mp3
    analysis.json
    ...
  app.db
```

### After

```
output/
  app.db
  projects/
    <project_id>/             (one per project, including Inbox)
      <video_id>/
        transcript.json
        audio.mp3
        analysis.json
        ...
      <other_video_id>/
        ...
      graphify-out/           (created on first graph build)
        graph.json
        index.html
        GRAPH_REPORT.md
        ...
      build.log               (last graphify run's output)
```

### Why `output/projects/` and not `output/<project_id>/`?

So we can keep top-level `output/` as the "data root" with `app.db`,
`_ingests.json`, etc. living next to a single `projects/` folder. No
naming collision risk between projects and bookkeeping files.

### Inbox folder

The Inbox project's folder is `output/projects/inbox/` (id is literally
`inbox`, system_kind='inbox'). Project ids are slug-cased; `inbox` is
reserved.

---

## Migration (one-shot)

`scripts/migrate_to_folder_per_project.py` performs the following.
Idempotent; rollback is possible until step 6.

```
1. Audit: count videos, project memberships, multi-project videos
   (must be 0 -- abort with explicit message otherwise). Print a
   before-table to stdout.

2. Create projects/inbox row if missing (system_kind='inbox',
   name='Inbox', non-deletable in CRUD).

3. For each video:
     - Determine target project_id:
         * if exactly one project_videos row exists, use that project
         * else assign to inbox
     - Compute target path: output/projects/<project_id>/<video_id>/
     - Skip if already at target path (idempotency)
     - Copy old path -> target path
     - Verify byte counts and checksums of every file
     - Update videos: SET project_id, path, updated_at

4. Drop project_videos table; clean up references in code (SELECTs and
   bulk_project_ids in transcripts.py).

5. Verify: every videos row has non-null project_id and path; folder
   exists at videos.path; old top-level <video_id>/ folder still
   exists (we'll delete in step 6).

6. Final: delete old top-level video folders (only after step 5
   verification passes for every row). Write an after-table to stdout.

Rollback (if any step fails before step 6):
- Discard new folders under output/projects/.
- Drop the new schema columns OR restore from a pre-migration .db
  backup taken in step 0.
```

The script writes a JSON manifest to `scripts/_migration_manifest.json`
recording every (old_path, new_path, project_id) so a manual rollback is
possible up to step 6.

**Pre-flight requirements before running migration:**
- Server stopped (no in-flight ingests).
- Free disk = at least the size of `output/`.
- Backup of `app.db` taken automatically (`app.db.pre-migration-bak`).

---

## Move protocol (runtime, post-migration)

Happens when the user reassigns a video to a different project.

```
move(video_id, new_project_id):

  PRE-CHECK:
    - Refuse if videos.move_state IS NOT NULL (concurrent move).
    - Refuse if there's an active ingest for this video (state.has_active).
    - Compute src_path from videos.path
    - Compute dst_path = output/projects/<new_project_id>/<video_id>/
    - Refuse if dst_path already exists.

  TRANSACTION 1:
    UPDATE videos SET move_state='moving' WHERE id=video_id;
    INSERT INTO move_log (video_id, from_project, to_project, from_path,
                          to_path, started_at, status='started');

  COPY:
    shutil.copytree(src_path, dst_path)

  VERIFY:
    For every file under src_path, confirm:
      - dst_path/<rel> exists
      - byte size matches
      - sha256 matches  (only for files <100MB; trust size for audio.mp3)
    UPDATE move_log SET status='verified' WHERE id=...;
    On failure: rmtree(dst_path), TX rollback (move_state -> NULL,
      move_log status='rolled_back'). Return error.

  TRANSACTION 2 (commit):
    UPDATE videos SET project_id=new_project_id, path=dst_path,
                      move_state=NULL WHERE id=video_id;
    UPDATE move_log SET status='committed', finished_at=NOW WHERE id=...;
    UPDATE projects SET events_since_build += 1 for both old & new project.

  CLEANUP:
    shutil.rmtree(src_path)
    (If this fails, the video is already correctly accessible from
     dst_path; the orphan source becomes garbage. Logged as a warning;
     a `scripts/garbage_collect_orphans.py` can clean it up later.)
```

### Crash recovery (boot-time)

`server.startup` runs `recover_in_flight_moves()` before serving requests:

```
For every videos row with move_state='moving':
  Look up the matching move_log row (status='started' or 'verified').

  Case A: status='started', src exists, dst does not exist
    -> Copy never finished. Clear move_state, status='rolled_back'.
       Video stays at src_path.

  Case B: status='started', both exist
    -> Copy may be partial. rmtree(dst_path); same as Case A.

  Case C: status='verified', both exist
    -> Verification succeeded but commit didn't. Re-run TRANSACTION 2,
       then cleanup.

  Case D: status='verified', only dst exists
    -> Move was effectively committed but DB never updated. Re-run
       TRANSACTION 2 with dst values.

  Case E: only one of src/dst exists, status='started'
    -> Inconsistent log. Trust whichever has a transcript.json that
       parses; mark the other for manual review (don't auto-delete).

  Log every recovery decision; status='committed'/'rolled_back' as
  appropriate.
```

### Concurrency guards

- `state.has_active(video_id)` returns true while ingest is writing.
  Move refuses while true.
- The ingest pipeline writes to `videos.path` (via a fresh DB read on
  each phase boundary), so a successful move that completes before the
  next phase boundary is invisible to the worker. No cross-process file
  locks needed for our single-uvicorn-worker setup.

---

## Project CRUD changes

### Create

Already takes `name` + optional `description`. Now also creates
`output/projects/<slug>/` on disk.

### Delete

Move every member video to Inbox (using the move protocol),
then delete the project row and the empty
`output/projects/<id>/` folder. Refuse to delete the Inbox itself
(`system_kind='inbox'`).

### Rename

DB-only. Slugs are immutable post-creation, so the folder name doesn't
change.

### Add video to project

Two paths:
- **Ingest with project pre-selected** (existing flow): video lands
  directly at `output/projects/<id>/<video_id>/`.
- **Reassign existing video**: triggers the move protocol.

### Remove video from project

Move to Inbox (via move protocol).

---

## Graphify integration

### Backend wrapper

New module `server/graphify.py`:

```
build_project_graph(project_id, mode='update' | 'rebuild') -> SSE generator
```

Internally:
- Resolves `output/projects/<project_id>/` as the corpus folder.
- Spawns `claude --print --output-format=stream-json --verbose` with the
  prompt:
  ```
  Run /graphify "<project_folder>" --update
  ```
  (or `--mode deep` / nothing for full rebuild).
- Streams Claude's events, translates `tool_use` / `text` chunks into
  our `phase` events. Mirrors how `server/ai/claude_cli.py` handles the
  analysis stream.
- On completion: parses `output/projects/<project_id>/graphify-out/graph.json`
  for node/edge counts, updates `projects.graph_state='ready'`,
  `graph_built_at`, `graph_node_count`, `graph_edge_count`,
  `events_since_build=0`.

### Endpoints

```
POST /api/projects/{project_id}/graph/build
  body: { mode: 'update' | 'rebuild' }
  returns: SSE stream

GET  /api/projects/{project_id}/graph/{*path}
  Static-serves files from output/projects/<id>/graphify-out/.
  Auth via the same require_http gate. Index defaults to index.html.

GET  /api/projects/{project_id}/graph/status
  returns: { state, built_at, node_count, edge_count, events_since_build,
             last_error }
```

### Events-since-build counter

Increment `projects.events_since_build` on:
- Ingest completes for a video belonging to the project.
- Video moved into the project (either side: src and dst projects both bump).
- Analysis written for a video belonging to the project.
- Video archived/restored within the project.

Reset to 0 when a graph build completes successfully.

---

## UI changes

### Sidebar

- Inbox appears in the projects list, marked with a different glyph
  (the existing folder icon vs. an inbox icon). Counter behavior
  identical.
- Right-click / context menu on Inbox: Rename allowed; Delete disabled
  with a tooltip.

### Project page

New section above the videos list:

```
KNOWLEDGE GRAPH
  state badge: never built / up to date / 5 events since last build / building / error
  [Build graph] / [Update graph] / [Open graph in new tab]
  meta: N nodes, M edges, last built 2 hours ago
```

`Open graph in new tab` links to `/api/projects/<id>/graph/index.html`
which static-serves graphify's HTML output.

While `graph_state='building'`, the section shows a progress strip with
the live SSE phase, similar to the analysis-progress widget on the
detail page.

### Detail page

A small badge near the title: `Project: <name>` (clickable -> goes to
project). Adds a "Move to project..." action in the existing action row
that opens a project picker; submit triggers the move protocol with
optimistic UI ("moving..." overlay until SSE confirms).

### Add Video / Ingest modal

New required field at top: project picker. Defaults to "Inbox" if no
project is selected, last-chosen otherwise.

---

## Implementation slices

Each slice ships with tests + a working build. No half-states left in
master.

### Slice 1 — schema + migration script

- Add `videos.project_id`, `videos.path`, `videos.move_state`,
  `projects.system_kind`, the graph fields, `move_log` table.
- Create the Inbox project on first boot (idempotent).
- Write `scripts/migrate_to_folder_per_project.py` (audit + dry-run +
  --apply).
- Run on dev DB; manually verify against current 74 videos.
- Tests: migration on a fixture DB, idempotency, rollback path.

### Slice 2 — server file-path layer

- Update `server/layout.py`:
  - `video_dir(out_dir, video_id)` becomes a DB-aware lookup that reads
    `videos.path` (with a small in-process cache to avoid roundtrips on
    hot paths like audio streaming).
  - Add `project_dir(out_dir, project_id)`.
- Update every caller (transcripts.py, transcriber.py, marks.py,
  podcast.py, main.py routes) to use the new helper.
- Backwards compat: if `videos.path` is null (pre-migration), fall back
  to `out_dir / video_id` -- so a server boot before migration still
  works.
- Tests: `test_layout` exercises both layouts.

### Slice 3 — move protocol + recovery

- Add `server.moves` module with `move_video(video_id, new_project_id)`.
- Wire boot-time `recover_in_flight_moves()` into the FastAPI startup.
- New endpoint `POST /api/videos/{id}/move` body `{project_id}`.
- Frontend: project picker on detail page; "moving..." overlay.
- Tests: happy path, every crash-recovery case (A-E), concurrent-move
  refusal, ingest-active refusal.

### Slice 4 — project CRUD updated

- Create-project also creates the folder.
- Delete-project moves members to Inbox first.
- Rename-project is DB-only.
- Inbox is non-deletable.
- Add-video flow: ingest takes optional project_id; defaults to Inbox.
- Tests: full project lifecycle including delete-with-members.

### Slice 5 — graphify integration

- `server/graphify.py` subprocess wrapper.
- New endpoints: `POST /api/projects/{id}/graph/build`, `GET .../graph/{path}`,
  `GET .../graph/status`.
- `events_since_build` counter wired into the existing event paths.
- Frontend: project page section + project graph viewer link.
- Tests: subprocess wrapper with a fake `claude` binary that emits
  canned events; SSE-translation correctness.

---

## Risks / non-goals / open questions

### Risks

1. **Migration on the live data is the highest-risk change in this
   project's history.** Mitigations: dry-run mode, automatic
   pre-migration `app.db` backup, manifest file, the script never
   deletes source folders until the after-table verification passes.
   The user runs migration with the server stopped, on a directory
   they can roll back from a backup.

2. **Path lookups become DB-bound.** Every audio-streaming request
   currently does an `os.path` join with the video_id; after migration
   it's a DB read. Mitigated by an in-process LRU cache keyed on
   video_id. Cache is invalidated on move.

3. **Browser caches old audio URLs after a move.** Mitigated by
   appending a `?v=<updated_at>` query param to media URLs so the
   browser refetches.

### Non-goals

- **Multi-project membership via secondary tags.** If you want a video
  in two projects later, that's a `tags[]` story, not membership.
- **Cross-machine project portability.** Folders are local. Sharing a
  project with someone else is out of scope.
- **graphify result caching/dedup across projects.** Each project has
  its own corpus; if two projects share a video (which they can't, post
  this change), they'd graph it twice. Fine.

### Resolved

1. **Slug collisions** — auto-suffix the second slug as `-2`, `-3`, ....
   Refuse only if the suffix space is exhausted (unlikely).
2. **Build vs Rebuild** — both buttons exposed. "Build graph" is the
   only option until the graph exists. Once it does, project page
   shows "Update graph" (runs `--update`) prominently and "Rebuild from
   scratch" (full re-extraction) behind a confirm dialog labelled
   "this re-runs semantic extraction and costs more".
3. **"Open graph" with no graph yet** — button is hidden until
   `graph_state in {'building','ready','error'}`. There is no link to
   click while the graph has never been built.

### Trigger surface (locked)

The user is the only thing that triggers a graph build. No background
debounce, no event-driven rebuild, no auto-trigger on first view. The
`events_since_build` counter is informational only -- it never causes a
build.

---

## Sign-off checklist

Before slice 1 lands:
- [ ] User has reviewed this spec and approved the structure.
- [ ] User has agreed to the migration timing (server-stopped, user-
      initiated).
- [ ] Open questions above are resolved.

After slice 5 lands:
- [ ] Every existing video resolves to its project folder via the new
      layout helper.
- [ ] Move protocol exercised end-to-end with a deliberate crash
      between TX1 and TX2 to verify recovery.
- [ ] Graphify build runs successfully on at least one real project
      and the HTML viewer opens in a new tab.
