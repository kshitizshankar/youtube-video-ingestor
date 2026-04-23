# Projects + Analysis v2 — Design

**Date:** 2026-04-23
**Status:** draft, owner: Claude (user delegated approval)
**Scope:** turn the ingestor into a project-organised tool with a dashboard, playlist import, parallel ingestion, and a pluggable "Understand with AI" step.

---

## 1. Goals

What a user can do when this ships:

1. **Create a project** from the dashboard. Give it a name + description.
2. **Add videos** to a project in either of two ways:
   - Paste a YouTube **playlist URL** → the app enumerates the playlist, user confirms which videos to import.
   - Paste one or more **video URLs** directly (newline- or comma-separated).
3. **Watch them get transcribed** as parallel as the local GPU allows. Progress is visible per video *and* as a project-level rollup.
4. **Manually trigger "Understand with AI"** on any single video, with a **model picker** (provider + model). No auto-analysis on ingest anymore. Output stays as today's `analysis.json` shape so existing consumers keep working.
5. **Open the app** and land on a **dashboard** showing: projects (most recent first), latest videos across projects, and basic content stats.

## 2. Non-goals (explicit)

- **Project-scoped AI chat / analysis.** Workspace features beyond membership are deferred.
- **Per-user isolation.** Still single-tenant. Schema leaves room (nullable `owner` column) so a future migration is an `ALTER TABLE`, not a rewrite.
- **Cloud-backed models.** Claude CLI stays as the only working provider in this slice; Codex and Ollama are interface stubs so extension is a small PR, not an architecture change.
- **Re-work of the transcription pipeline itself.** Whisper / WhisperX stay as-is; we wrap them in a queue.
- **Rich dashboard analytics.** Simple counts only (videos, hours, projects, storage). No charts library.

## 3. Success criteria

Each slice is done only when these hold:

| Check | How we verify |
|---|---|
| Existing `output/` data migrates into SQLite without loss | pytest integration test runs migration on a fixture tree and asserts DB rows match |
| Projects CRUD API works round-trip | pytest hitting FastAPI via `httpx.AsyncClient` |
| Dashboard renders with real data | Playwright test logs in, loads `/`, asserts project + video counts |
| Playlist import enumerates videos without downloading | Unit test with mocked `yt-dlp` returning an `extract_flat` payload |
| Parallel ingestion respects a configurable cap | pytest drives the queue with mocked worker and asserts max-in-flight |
| "Understand with AI" no longer auto-runs on ingest | Pipeline test: ingest finishes with `analysis.json` absent |
| Provider abstraction works with a fake provider | Register a `FakeProvider`, trigger analyze, assert events + saved JSON |
| No regressions on existing routes | Existing flows (library list, archive, search, SSE transcribe, PTY) remain functional — smoke-tested in Playwright |

If any check fails, the slice is not done. No "we'll fix it later."

## 4. Architecture — diff from today

```
┌──────────────────────────────────────────────────────────────────────┐
│ today                                                                │
│  output/<id>/{transcript.json, .txt, .srt, audio.mp3, CLAUDE.md,     │
│               analysis.json, meta.json} + output/_ingests.json       │
│  transcribe() → writes files → ALSO calls analyze()                  │
│  claude CLI hardcoded in analyze.py                                  │
│  no project concept                                                  │
└──────────────────────────────────────────────────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────────────────────────┐
│ after                                                                │
│  output/app.db (SQLite: videos, projects, ingests, analyses, ...)    │
│  output/<id>/{transcript.json, .txt, .srt, audio.mp3, CLAUDE.md,     │
│               analyses/<id>.json + analysis.json symlink}            │
│  ingest queue (ThreadPoolExecutor, cap = N)                          │
│  transcribe() → writes files + DB row. No auto-analyze.              │
│  analyze() is user-triggered, dispatched through                     │
│      AnalysisProvider registry (Claude / Codex / Ollama)             │
│  projects (many-to-many with videos)                                 │
│  dashboard at "/"; Library at "/library"                             │
└──────────────────────────────────────────────────────────────────────┘
```

## 5. Data model — SQLite

File: `output/app.db`. Single file, committed to a single process (same constraint as `_ingests.json` today).

```sql
-- Videos: one row per YouTube video we know about.
-- `id` is YouTube's 11-char id. Blobs still live on disk under output/<id>/.
CREATE TABLE videos (
    id                            TEXT PRIMARY KEY,
    url                           TEXT NOT NULL,
    title                         TEXT,                     -- currently YouTube-sourced; user-overridable via PATCH /meta
    channel                       TEXT,
    channel_id                    TEXT,
    channel_url                   TEXT,
    channel_follower_count        INTEGER,
    upload_date                   TEXT,                     -- YYYYMMDD
    duration_sec                  REAL,
    description                   TEXT,                     -- first 1000 chars (matches current cap)
    categories                    TEXT,                     -- JSON array
    yt_tags                       TEXT,                     -- JSON array (YouTube's own tags, distinct from user tags)
    view_count                    INTEGER,
    like_count                    INTEGER,
    comment_count                 INTEGER,
    language                      TEXT,
    language_probability          REAL,
    diarized                      INTEGER NOT NULL DEFAULT 0,
    speaker_count                 INTEGER,
    segment_count                 INTEGER,
    -- Transcription run details
    model                         TEXT,
    compute_type                  TEXT,
    batched                       INTEGER,
    batch_size                    INTEGER,
    transcription_elapsed_sec     REAL,
    transcription_realtime_factor REAL,
    -- Bookkeeping
    storage_bytes                 INTEGER,                  -- sum of per-video folder; updated at ingest-done
    archived                      INTEGER NOT NULL DEFAULT 0,
    notes                         TEXT,                     -- from meta.json
    owner                         TEXT,                     -- NULL today; used later for multi-user
    transcribed_at                TEXT,                     -- ISO8601
    created_at                    TEXT NOT NULL,            -- ISO8601
    updated_at                    TEXT NOT NULL
);
CREATE INDEX idx_videos_created_at ON videos(created_at);
-- Partial index: most videos are live, only a small set is archived.
CREATE INDEX idx_videos_archived   ON videos(archived) WHERE archived = 1;

-- Free-form user tags (keeps existing tags behaviour).
CREATE TABLE video_tags (
    video_id  TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    tag       TEXT NOT NULL,
    PRIMARY KEY (video_id, tag)
);

-- Speaker rename overrides (today: meta.json.speaker_names).
CREATE TABLE video_speakers (
    video_id  TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    label     TEXT NOT NULL,             -- "SPEAKER_00"
    name      TEXT NOT NULL,             -- "Lex"
    PRIMARY KEY (video_id, label)
);

-- Projects.
CREATE TABLE projects (
    id           TEXT PRIMARY KEY,       -- slug (short human-readable id)
    name         TEXT NOT NULL,
    description  TEXT,
    owner        TEXT,                   -- NULL today
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- Membership. Many-to-many. A video can belong to multiple projects.
CREATE TABLE project_videos (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    video_id    TEXT NOT NULL REFERENCES videos(id)   ON DELETE CASCADE,
    added_at    TEXT NOT NULL,
    PRIMARY KEY (project_id, video_id)
);
CREATE INDEX idx_pv_project ON project_videos(project_id);
CREATE INDEX idx_pv_video   ON project_videos(video_id);

-- Ingestion job registry. Surrogate PK so retry creates a *new* row (history
-- preserved) and rekey (pending-xxxx → real id) is a plain UPDATE without
-- colliding against a pre-existing row for the same video.
CREATE TABLE ingests (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id          TEXT NOT NULL,     -- "pending-xxxx" until rekey after download
    url               TEXT,
    title             TEXT,
    duration_sec      REAL,
    phase             TEXT NOT NULL,     -- queued / downloading / transcribing / ...
    started_at        REAL NOT NULL,     -- unix epoch seconds (keeps state.py shape)
    last_event_at     REAL NOT NULL,
    segments          INTEGER NOT NULL DEFAULT 0,
    last_segment_end  REAL NOT NULL DEFAULT 0,
    done              INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    cancel_requested  INTEGER NOT NULL DEFAULT 0,
    project_id        TEXT REFERENCES projects(id) ON DELETE SET NULL
);
CREATE INDEX idx_ingests_video     ON ingests(video_id);
-- Fast "is there an active job for this video?" check.
CREATE INDEX idx_ingests_active    ON ingests(video_id) WHERE done = 0;

-- Analysis runs. Each run writes to its own file (`analyses/<analysis_id>.json`)
-- under output/<video_id>/ so history survives. The current "latest" file is
-- whichever row has the max(started_at) with status=done.
CREATE TABLE analyses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,          -- "claude_cli"
    model        TEXT,                   -- free-text model id per provider
    status       TEXT NOT NULL,          -- running / done / error / cancelled
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    cost_usd     REAL,
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    error        TEXT,
    file_path    TEXT                    -- e.g. "analyses/7.json"; NULL if never produced an artifact
);
CREATE INDEX idx_analyses_video ON analyses(video_id);

-- Migration tracker. Hand-rolled runner (see §11): ordered SQL files in
-- server/migrations/, applied exactly once; filenames match `applied_at`.
CREATE TABLE schema_migrations (
    name        TEXT PRIMARY KEY,       -- e.g. "001_initial"
    applied_at  TEXT NOT NULL           -- ISO8601
);
```

**SQLite PRAGMAs set on every connection open (critical, not optional):**

- `PRAGMA journal_mode = WAL;` — concurrent readers + single writer; segment-rate writes don't block `/api/ingests` polls.
- `PRAGMA synchronous = NORMAL;` — fsync on checkpoint, not every commit. Crash-safe enough for our use.
- `PRAGMA foreign_keys = ON;` — **required** or every `ON DELETE CASCADE` in the schema is a silent no-op.
- `PRAGMA busy_timeout = 5000;` — 5s wait instead of instant `SQLITE_BUSY` on contention.

**Rationale for the shape:**
- `videos` denormalises every indexable field from `transcript.json` + `meta.json`. `segments` stays in `transcript.json` on disk (too large for a row; also canonical there).
- `storage_bytes` is pre-computed at ingest-done so the dashboard's storage total is `SELECT SUM(storage_bytes)`, not a filesystem walk. Migration populates it with a one-time `du` per folder.
- Tags + speakers as side tables, not JSON columns: cheap to query ("all videos with tag X"), and survives re-ingest.
- `ingests` uses a surrogate INTEGER PK so a retry creates a *new* row (history preserved) and the rekey flow (`pending-xxxx → real id`) is a plain UPDATE, not a PK swap. `state.py` keeps an in-memory hot cache and only persists on **phase transitions** (not every segment) — segment counters increment in-memory and flush on phase change, error, done, or every 2s max. Matches today's debounce but across the DB boundary.
- `analyses` table with per-run files so history doesn't clobber itself. The latest-run JSON is looked up via `SELECT file_path FROM analyses WHERE video_id=? AND status='done' ORDER BY started_at DESC LIMIT 1`; the on-disk file is `output/<video_id>/analyses/<analysis_id>.json`. A stable symlink `output/<video_id>/analysis.json → analyses/<latest>.json` is written for backward-compatible consumers.

## 6. Storage: what's on disk vs. DB

**On disk (unchanged):**
- `output/<video_id>/audio.mp3`
- `output/<video_id>/transcript.json` (segments + everything returned by Whisper)
- `output/<video_id>/transcript.txt`
- `output/<video_id>/transcript.srt`
- `output/<video_id>/CLAUDE.md`
- `output/<video_id>/analysis.json` (latest analysis; historical rows live in DB)

**In SQLite:**
- Everything *relational* or *indexed*: videos metadata, projects, membership, tags, ingest jobs, analyses.

`meta.json` goes away (its fields move to `videos.notes`, `video_tags`, `video_speakers`).
`_ingests.json` goes away (replaced by `ingests` table).

## 7. API surface

### Existing routes that move

| Today | Tomorrow | Notes |
|---|---|---|
| `GET /api/transcripts` | same | Reads from `videos` (live, non-archived) |
| `GET /api/transcripts/{id}` | same | Joins `videos` + reads `transcript.json` from disk |
| `GET /api/archive` | same | |
| `POST /api/transcripts/{id}/archive` | same | Updates `videos.archived` |
| `GET/PATCH /api/transcripts/{id}/meta` | same | Reads/writes `videos.notes`, `video_tags`, `video_speakers` |
| `GET /api/transcripts/{id}/analysis` | same | Reads latest `analyses` row + JSON from disk |
| `POST /api/transcripts/{id}/analyze` | **changed** | Body: `{ provider: "claude_cli", model: "claude-opus-4-7" }`; returns 202 + `analysis_id` |
| `GET /api/transcripts/{id}/analyze/stream` | same shape | Subscribes by `analysis_id` query param; events unchanged |
| `GET /api/ingests` | same | Reads from `ingests` table |
| `POST /api/ingests/{id}/retry` | same | |
| `POST /api/ingests/{id}/cancel` | same | |
| `DELETE /api/ingests/{id}` | same | |
| `GET /api/transcribe` (SSE) | **deprecated** | Replaced by `POST /api/ingests` + `GET /api/ingests/{id}/stream` |

### New routes

```
# Projects
GET    /api/projects                              → [{id, name, description, video_count, created_at}]
POST   /api/projects                              → body: {name, description?} → created project
GET    /api/projects/{id}                         → {project, videos: [...]}
PATCH  /api/projects/{id}                         → body: {name?, description?}
DELETE /api/projects/{id}                         → removes project; videos are untouched

# Membership
POST   /api/projects/{id}/videos                  → body: {video_ids: [...]} add; idempotent
DELETE /api/projects/{id}/videos/{video_id}       → remove from project (video stays)

# Playlist preview (no downloads)
POST   /api/playlist/preview                      → body: {url}
                                                    response: {
                                                      playlist_id, title, uploader,
                                                      videos: [{id, title, duration, thumbnail_url}]
                                                    }

# Bulk ingest (replaces single-shot GET /api/transcribe)
POST   /api/ingests                                → body: {
                                                      project_id?,
                                                      urls: [...],          # explicit URLs OR
                                                      playlist_url?,        # full playlist (expanded server-side)
                                                      options: { diarize?, model?, batched? }
                                                    }
                                                    response: { job_ids: [...], skipped: [...] }
                                                    # skipped: already-transcribed video ids (still added to project if project_id given)

# Per-job SSE stream (replaces the URL-driven GET /api/transcribe)
GET    /api/ingests/{id}/stream                    → SSE: same event shape as today (phase, segment, done, error, heartbeat)

# AI provider discovery (for the model picker UI)
GET    /api/ai/providers                           → [
                                                      {name: "claude_cli", available: true,
                                                       models: ["claude-opus-4-7", "claude-sonnet-4-6", ...]},
                                                      {name: "codex_cli",  available: false, reason: "binary not on PATH"},
                                                      {name: "ollama",     available: true,  models: [...]}
                                                    ]

# Dashboard stats
GET    /api/stats                                  → {
                                                      video_count, project_count,
                                                      total_hours, storage_bytes,
                                                      latest_videos: [...]
                                                    }
```

SSE transcribe stream stays — a running job's owner tab still subscribes to `GET /api/ingests/{id}/stream` (new name; same event shape as today's `/api/transcribe`).

## 8. AI provider abstraction

The key insight from review: Claude CLI, Codex CLI, and Ollama have **different native event envelopes**. Forcing one shape onto all three means `if provider == "claude_cli"` branches in the frontend. Instead: providers emit *normalized* events. The frontend renders the normalized shape; providers own the translation.

```python
# server/ai/provider.py

class AnalysisEvent(TypedDict, total=False):
    """Normalized event emitted by every provider. UI renders this shape."""
    type: Literal["stage", "progress", "usage", "done", "error"]
    # stage: a semantic step label ("reading transcript", "composing answer",
    #        "running tool: grep"). UI shows these as a live timeline.
    stage: str
    # progress: free-form status text for the current stage (optional sub-lines).
    message: str
    # usage: incremental token/cost updates (providers emit as often as they have data).
    tokens_in: int
    tokens_out: int
    cost_usd: float
    cached_tokens: int
    # done: final payload — parsed analysis JSON + metadata.
    result: dict[str, Any]
    file_path: str        # where the artifact was written
    duration_ms: int
    # error:
    error_message: str

class AnalysisProvider(Protocol):
    name: str                                   # "claude_cli", "codex_cli", "ollama"
    display_name: str                           # "Claude Code CLI"
    def available(self) -> tuple[bool, str | None]: ...   # (ok, reason-if-not)
    def list_models(self) -> list[str]: ...
    def stream_analyze(
        self,
        *,
        video_folder: Path,
        prompt: str,
        model: str | None,
        cancel_event: threading.Event,
    ) -> Iterator[AnalysisEvent]: ...
```

**Per-provider translation notes:**

- **`ClaudeCLIProvider`** — runs `claude -p ... --output-format=stream-json --verbose`. Translates:
  `system/init → stage="Starting session"`, `assistant.tool_use → stage="Running tool: <name>"`, `assistant.text → stage="Composing answer"`, `result → done` with parsed JSON. Usage events emitted per assistant turn.
- **`CodexCLIProvider`** — runs `codex exec` (or equivalent) with its own stream flags. Codex's native events (session/patch/exec) map to `stage` labels; there's no 1:1 parity with Claude's tool_use shape, so we translate what makes sense and drop what doesn't. **Stub only in this slice** — marked unavailable via `available()` until user has a binary to test.
- **`OllamaProvider`** — POST `http://localhost:11434/api/chat` with `stream=true`. Emits one `stage="Generating"` then token-level updates; no tool use. Available iff the Ollama daemon responds to `/api/tags`. No gating flag — `available()` is the only switch.

Registry: `server/ai/__init__.py` exposes `get_provider(name)` and `list_providers()`. New providers plug in by appending to the registry.

**Cancellation (Windows-specific, because that's what the user runs):**
Python's `proc.terminate()` = `TerminateProcess` on Windows, which does **not** kill the child tree. `claude` spawns a Node runtime; `terminate()` orphans it. The provider spawns subprocess with `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` and on cancel calls `proc.send_signal(signal.CTRL_BREAK_EVENT)`, then walks the process tree via `psutil` (new dep) to kill stragglers after a 2s grace period. Linux/macOS fallback: `os.killpg(-proc.pid, SIGTERM)` then `SIGKILL`. Cancel latency: ≤2s from button press.

## 9. Concurrency — parallel ingestion

- **One queue**, one in-process worker pool. `MAX_CONCURRENT_INGESTS` env var, default `1` (GPU is the bottleneck, more than 1 concurrent Whisper run hurts throughput on a single RTX).
- Stages inside one ingest (`download → transcribe → diarize → write`) stay serial; they already share state per job.
- Downloads run inside the queue worker slot, not outside — simpler, and `bestaudio` downloads are fast enough not to warrant a separate pool.
- Analysis runs are **not** on the ingest queue. They have their own worker (so a user can kick off "Understand" while an ingest is in-flight without deadlock). One in-flight analysis per video; the button disables while running.

**Queue implementation:** a `concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_INGESTS)` lives in a new module `server/queue.py`. `POST /api/ingests` enqueues — the handler returns **immediately** after the enqueue (response: `{job_ids}`); the executor pulls. The handler never awaits the future. Progress is reported exclusively through the SSE stream and the `/api/ingests` poll. This is explicit because `await future.result()` inside an async handler deadlocks the event loop once the pool is full.

Queued jobs get `phase="queued"` in the `ingests` table immediately so they show up in the UI. The orphan-detection logic on startup (today: any non-done row → orphaned) is extended to also mark `queued` rows as orphaned — the executor is gone with the previous process, and a surviving queued row would otherwise hang forever.

## 10. UI surface (MVP)

Landing screen = **Dashboard** at `/`. Today's Library moves to `/library` (still accessible from sidebar).

### Dashboard `/`

Sections, top to bottom:

1. **Hero / greeting** with CTA: "New project" + "Quick ingest" (no-project ingest still works).
2. **Your projects** — grid of cards. Each shows name, description, video count, total hours, a tiny stacked thumbnail strip (up to 4 thumbs), last-activity date. Click → `/p/<slug>`.
3. **Latest videos** — list (most recent 8 across all projects).
4. **Stats strip** — totals: N videos, X hours, Y projects, Z GB.

Design treatment: I will invoke `frontend-design:frontend-design` when building this, not just scaffold generic cards. The user explicitly flagged UI quality.

### Project page `/p/<slug>`

- Top bar: project name (editable inline), description, "Add videos" button, "Rename", "Delete".
- Add-videos flow: modal with two tabs — **Playlist URL** vs **Video URLs**. Playlist tab triggers `/api/playlist/preview`, shows the video list, user selects (all selected by default), confirms → enqueue.
- Below: video rows (reusing `VideoRow` component). Per-row status: "Transcribing 35%", "Done", "Failed", etc.
- Inline stats: N videos, total hours, how many are still ingesting.

### Video detail `/v/<id>`

Unchanged layout. Two changes:
- Analysis pane no longer auto-populates. Shows an empty state with an **"Understand with AI"** button.
- Clicking the button opens a small modal: provider dropdown, model dropdown (populated from `/api/ai/providers`), submit.
- Progress UI during analysis: stream events displayed step-by-step. This gets the `frontend-design` treatment too — it's the "good UI that gives them feedback" the user asked for.

### Sidebar

- Dashboard
- Library (all videos, flat)
- Projects (expandable list of projects; quick jump)
- Archive
- New ingest

## 11. Migration strategy

### Schema migrations (code)

Ordered SQL files in `server/migrations/`: `001_initial.sql`, `002_*.sql`, ... Each file is a single transaction. A tiny hand-rolled runner on startup:

```python
# Pseudo: server/db/migrate.py
def run_migrations(conn):
    applied = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    for path in sorted((MIGRATIONS_DIR).glob("*.sql")):
        name = path.stem
        if name in applied:
            continue
        with conn:   # transaction
            conn.executescript(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations(name, applied_at) VALUES(?,?)",
                (name, iso_now()),
            )
```

No Alembic — overkill. The table `schema_migrations` *is* the state.

### Data migration (one-shot, from existing JSON)

Shipped as `001_initial.sql` creates the tables + a follow-up **Python** pass `server/db/migrate_data.py` that:

1. Detects `output/*/transcript.json` folders. For each:
   - Upsert a `videos` row (every field listed in §5, populated from transcript.json + meta.json).
   - Compute `storage_bytes` from `du -s` equivalent on the folder.
   - Copy `meta.json.tags` → `video_tags`, `meta.json.speaker_names` → `video_speakers`, `meta.json.notes` → `videos.notes`.
2. Loads `output/_ingests.json` → inserts into `ingests` with new surrogate PK; every row is marked `done=1, error='orphaned (pre-migration)'` since the workers are gone.
3. Existing `analysis.json` files create one synthetic `analyses` row (`provider='claude_cli', model='legacy', status='done'`, `file_path="analysis.json"`) — then rename the file to `analyses/1.json` and create the compatibility symlink `analysis.json → analyses/1.json`. Symlinks on Windows need admin or Developer Mode enabled — fallback is a plain file copy, with a startup warning logged if neither works.
4. Writes a `schema_migrations` row `('data_migration_v1', <ts>)` so it doesn't re-run.
5. **Does not delete** any source files. `meta.json` and `_ingests.json` stay on disk as a read-only fallback. Post-migration cleanup is a manual `POST /api/maintenance/cleanup-legacy-files` (no UI in this slice).

Rollback plan: the user's `output/` directory is unchanged; deleting `output/app.db` reverts to the old behaviour.

**Migration test:** `tests/test_migration.py` drops a representative fixture tree under `tmp_path`, runs migration, asserts row counts, tag preservation, ingest orphaning, analysis row + file move. If the test fails, migration is broken — blocks slice ship.

## 12. Testing strategy

### Backend — pytest

Dependency adds (in `pyproject.toml`, dev group):
- `pytest`, `pytest-asyncio`, `httpx`, `pytest-mock`, `anyio`

Test layout:

```
tests/
  conftest.py             # fixtures: temp OUTPUT_DIR, in-memory SQLite, fake provider, AUTH_DISABLED
  test_migration.py       # fixture tree → run migration → assert DB state (tags, speakers, notes, storage_bytes,
                          #   analysis row + file move, ingests orphaned)
  test_projects_api.py    # CRUD + membership round-trips; slug collision retry
  test_playlist.py        # mocked yt-dlp extract_flat; 500-entry cap; channel rejection; per-entry failures
  test_queue.py           # worker-cap enforcement; queued-on-restart orphaning; handler returns immediately
  test_providers.py       # Claude/Fake providers register; event translation; dispatch via registry
  test_provider_stubs.py  # Codex + Ollama stubs instantiate + list_models without raising
  test_analyze_trigger.py # ingest finishes → analysis.json absent AND no `analyses` row AND
                          #   fake provider's call count is zero (belt + braces)
  test_cancel.py          # cancel-during-analyze: subprocess tree gone within 2s (uses a sleep-hung binary)
  test_sse.py             # SSE stream termination semantics (httpx + asyncio.wait_for)
  test_dedup.py           # video-in-DB + same-URL ingest → skipped path; archived rejected; force=true re-runs
  test_pragmas.py         # connection opens with WAL, foreign_keys=ON, synchronous=NORMAL, busy_timeout=5000
```

### Frontend — Playwright

- `web/e2e/dashboard.spec.ts` — load `/`, assert empty state; create project; verify it appears.
- `web/e2e/project_flow.spec.ts` — add videos by URL (pipeline mocked via test server), watch status progress, verify completion.
- `web/e2e/analyze_manual.spec.ts` — open video, click Understand-with-AI (with fake provider), verify events + final state.
- `web/e2e/library_migration.spec.ts` — boot with fixture `output/`, verify existing videos show up after migration.

**Auth bypass for tests:** a new env var `AUTH_DISABLED=1` short-circuits `auth.is_enabled()` to `False`. Only ever set in test mode; the login gate is not exercised in e2e (separate, smaller test covers it).

**SSE testing recipe** (pytest): `httpx.AsyncClient.stream()` + `async for line in resp.aiter_lines()` with a `pytest.approx`-bounded timeout (`asyncio.wait_for`, 10s). Tests use the `fake` provider which emits `done` within milliseconds — no real backpressure. The fake provider is registered via `AI_PROVIDER=fake` and forces `get_provider("fake")` to the test implementation regardless of availability checks.

Run locally via `npm run e2e` — spins up FastAPI with `AUTH_DISABLED=1` and `AI_PROVIDER=fake` env.

### What we're NOT testing

- Real Whisper / yt-dlp downloads (too slow, flaky). Integration happens by hand on the user's machine.
- Real Claude CLI (cost, unpredictable). Fake provider stands in.

### CI

**Not set up in this slice.** All tests run locally. If/when CI arrives, the Playwright browser binaries (~400MB) are the main overhead; we'd cache them. Noted so nobody assumes green-on-push.

## 13. Slicing / shipping plan

| Slice | Contents | Why this order |
|---|---|---|
| **0. Stop auto-analysis** | Remove the in-pipeline call to `stream_analyze_video` from `transcriber.py`. Existing manual `POST /transcripts/{id}/analyze` keeps working. One pytest: ingest → `analysis.json` absent. | Ships in an hour, directly answers the user's #3 priority, unblocks them immediately without waiting on the foundation. |
| **1. Foundation** | SQLite schema + migration runner + data migration, projects CRUD API, Dashboard at `/`, scoped Library, tests (pytest + initial Playwright). | Everything downstream depends on the data model. |
| **2. Import & parallel ingest** | Playlist preview, bulk `POST /api/ingests`, ThreadPoolExecutor queue, project-page "Add videos" flow. | Delivers the "container" experience — user can feel the shape. |
| **3. Analysis v2** | Provider abstraction, Claude CLI refactor, Codex/Ollama stubs, manual trigger modal with model picker, polished progress UI via `frontend-design`. | Most visible polish; builds on the foundation so per-project defaults are a small follow-up. |

Each slice ships as its own commit + smoke test; the user gets a running tool to play with between slices.

## 13b. Hot-path state write amplification (addressed explicitly)

Today: `state.segment_received` fires hundreds of times per second in batched mode; `state.py` deliberately skips persisting on each call (persist on phase transitions only). If we move naively to SQLite, a `BEGIN COMMIT` per segment would thrash under WAL even with `synchronous=NORMAL`.

Plan:
- Hot cache stays in memory (dict keyed by `ingest_id` — the new surrogate PK). Reads hit the cache; the DB is the durable-but-lagged store.
- Writes to DB happen on: phase transitions, error, done, cancel, or every **2s of wall time** (coalesced). Segment counters update in memory each event, flush on timer.
- `list_active()` reads from cache, not DB — polling stays free.
- On graceful shutdown, a `finalize()` flushes the cache to DB. On crash, up to ~2s of segment counts are lost, which is inconsequential (the counts get rewritten on retry if needed).

## 14. Risks & tradeoffs (surfaced per Karpathy #1)

1. **Migration correctness is load-bearing.** If we lose user tags or speaker renames during migration, that's data loss. Mitigation: source files are untouched; automatic DB backup before data migration; representative fixture in the migration test; explicit assertions for tags/speakers/notes round-trip.
2. **Parallelism may actively hurt on one GPU.** Default `MAX_CONCURRENT_INGESTS=1` means the user doesn't see parallel transcriptions until they opt in. We can later measure and raise the default. I flag this explicitly because the user asked for "as parallel as possible on my GPU locally" — the honest answer is "single-Whisper-at-a-time is usually faster for throughput; we parallelise *other* stages".
3. **Removing auto-trigger changes the existing behaviour.** Users who expected analysis-on-ingest will see a new empty state. This is what the user asked for — documented in the release note. Shipped in Slice 0.
4. **Provider stubs could rot.** Codex + Ollama providers that aren't exercised will drift. Mitigation: `available()` gates so a broken stub doesn't show up in the UI; `tests/test_provider_stubs.py` imports + instantiates each and calls `list_models()` with a mocked network/subprocess.
5. **Subprocess kill on cancel — solved per §8.** Windows `CREATE_NEW_PROCESS_GROUP` + `CTRL_BREAK_EVENT` + `psutil` tree-kill fallback. Test `test_cancel.py` spawns a fake long-running subprocess and asserts the tree is gone within 2s. Leftover state on the Anthropic side (e.g. a half-finished turn) is out of our control.
6. **No per-user isolation yet.** `owner` columns today + a future `project_collaborators` table for sharing. **Not** a pure `ALTER TABLE` story — flagged here so nobody sells the migration as smaller than it is.
7. **Playwright is new to this repo.** Adds ~400MB of browser binaries. CI is not set up in this slice — tests run locally only.
8. **Symlinks on Windows for the `analysis.json` compat shim.** Requires admin or Developer Mode; fallback is a plain file copy with a startup warning. If the user runs without either, consumers of `analysis.json` see a stale (last-copied) file. Acceptable because the canonical path is `analyses/<id>.json` and the symlink exists purely to avoid breaking any unknown external script.
9. **SQLite segment-write contention.** WAL + `synchronous=NORMAL` + 2s debounced flush (§13b) rather than naive per-event writes. If this still manifests under load (measurable via `sqlite3_profile` or `EXPLAIN QUERY PLAN`), we increase the debounce interval or drop segment counters to phase-boundary only.

## 15. Disambiguations baked in

Decisions made explicit so implementation doesn't drift:

- **Project ids** are short slugs derived from the name (`lowercase-with-dashes`), max length 48 chars. Collision handling uses `INSERT ... ON CONFLICT DO NOTHING` in a retry loop — the first INSERT that succeeds wins; subsequent retries append `-2`, `-3`, etc. Race-safe. The canonical id is stable — renaming the project does **not** rename the id.
- **Duplicate ingest of a video already in DB**:
  - Video exists, **not** archived → added to target project (if `project_id`) without re-transcription; appears in `skipped` array; UI toast "already transcribed — added to project".
  - Video exists, **archived** → rejected with an explicit error `{already_archived: [...]}` in the response; user must restore-from-archive first (existing `POST /transcripts/{id}/restore`).
  - Client wants to re-transcribe (e.g., different Whisper model) → `POST /api/ingests` accepts `options.force=true` which deletes the row and re-runs. Without `force`, we never redo work.
- **Playlist enumeration** uses `yt-dlp` with `extract_flat='in_playlist'` — metadata only, no downloads. Hard cap at **500 entries**; anything over returns an error with the count so the user picks a sub-playlist. Channel-feed URLs (that yt-dlp *also* expands) are rejected with "this looks like a channel; give me a playlist URL or paste video links instead". Per-entry failures (private/deleted/region-blocked) land in a `failures: [{id, reason}]` field of the preview; importable videos are still returned.
- **`MAX_CONCURRENT_INGESTS`** is a single env var. No per-stage tuning in this slice; we measure first, add knobs if needed.
- **Sidebar "Projects"** shows the top 8 by recent activity; clicking "Show all" navigates to `/projects` (a flat list page). Keeps the sidebar from growing unbounded.
- **Video title editing.** `PATCH /api/transcripts/{id}/meta` accepts a new `title` field; updates go to `videos.title`. The existing `refresh-metadata` endpoint overwrites with the YouTube-fetched title (destructive, user-initiated). No separate override column.
- **Multi-user is not just `ALTER TABLE`.** Spec promises an `owner` column as the seam; the first multi-user feature will additionally add a `project_collaborators(project_id, user_id)` table for sharing. Keeping this honest so we don't wave at "future ALTER" and discover a larger scope later.
- **`CLAUDE.md` per video** stays named as-is — it's a Claude-CLI project file that the PTY endpoint depends on. When Codex/Ollama land, we may render a provider-agnostic companion (`NOTES.md`) alongside, but that's out of scope here.
- **Race between `PATCH /meta` and worker writes.** Every write path runs inside `BEGIN IMMEDIATE / COMMIT` — SQLite's transaction model serialises them. `updated_at` reflects the committing writer's time. A concurrent read never sees partial state.
- **Backup.** A new `POST /api/maintenance/backup-db` (admin-y, no UI) copies `output/app.db` to `output/backups/app-<timestamp>.db` using SQLite's online backup API. Called by the data-migration runner before it mutates anything, and recommended in README before any manual DB surgery.

## 16. Open questions (none blocking)

None. The user delegated approval; if anything surfaces during implementation that genuinely contradicts the spec, I pause and surface it rather than silently reinterpret.
