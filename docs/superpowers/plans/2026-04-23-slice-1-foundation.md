# Slice 1: SQLite Foundation + Projects + Dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SQLite as the canonical relational store, migrate existing `output/` JSON into it, ship projects (create/rename/delete + video membership), replace the Library home with a Dashboard, and wire new API surfaces for projects + stats. Ingestion keeps working; analysis path unchanged.

**Architecture:** SQLite file at `output/app.db`. Hand-rolled migration runner (ordered `.sql` files under `server/migrations/`) + one-shot Python data migrator that walks `output/*/transcript.json` on first run. Existing `transcripts.py` / `meta.py` / `state.py` internals flip to DB-backed; on-disk blob layout is unchanged. New `server/db.py` centralises connection + PRAGMAs; new `server/projects.py` is the projects service. React gets a `/` Dashboard, `/library` (old Library moved), and `/p/:slug` project page.

**Tech Stack:** SQLite via Python's stdlib `sqlite3`, FastAPI, React 19 + react-router 7, pytest + pytest-mock.

---

## Pre-flight

Before starting, confirm:
- `uv run pytest tests/` passes (Slice 0 tests green: 2 passed)
- Working tree is clean (`git status` shows nothing)
- Latest commit is `9b9a7e5` ("Slice 0: stop auto-triggering Claude analysis on ingest")

---

## File map

**New files:**
- `server/db.py` — connection factory + PRAGMAs + tiny query helpers
- `server/migrations/001_initial.sql` — DDL (all tables)
- `server/migrate_data.py` — one-shot JSON → SQLite migration
- `server/projects.py` — projects CRUD + membership service
- `tests/test_db.py` — PRAGMAs + migration runner
- `tests/test_migrate_data.py` — data migration against fixture tree
- `tests/test_projects_api.py` — CRUD + membership + slug collision
- `tests/test_videos_db.py` — videos repo round-trips through DB
- `tests/test_meta_db.py` — tags/speakers/notes round-trip via DB
- `tests/test_stats_api.py` — `/api/stats` totals
- `tests/fixtures/migration_tree/` — minimal `output/` tree for migration tests
- `web/src/Dashboard.tsx` — new home
- `web/src/Project.tsx` — per-project page (read-only in this slice)
- `web/src/components/ProjectCard.tsx`
- `web/src/components/StatsStrip.tsx`
- `web/src/components/NewProjectModal.tsx`
- `web/src/projects.ts` — fetch helpers for projects + stats

**Modified files:**
- `server/transcripts.py` — internals read/write DB instead of disk scan
- `server/meta.py` — internals read/write DB tables
- `server/transcriber.py` — upsert `videos` row + `video_tags`/`video_speakers` on done; update `storage_bytes`
- `server/main.py` — run migrations at startup; add `/api/projects*`, `/api/stats`; existing routes keep their URL shape
- `web/src/App.tsx` — `/` → Dashboard, `/library` → Library, `/p/:slug` → Project
- `web/src/Library.tsx` — unchanged logic; moved route
- `web/src/components/Sidebar.tsx` — add Dashboard + Projects links
- `web/src/api.ts` — expose stats + projects clients (re-export from projects.ts)
- `web/src/types.ts` — add `Project`, `Stats`, `ProjectVideoSummary` types

**Unchanged but worth knowing:** `server/layout.py`, `server/analyze.py`, `server/state.py` (state stays in-memory this slice; DB backing is Slice 2). `server/auth.py` unchanged.

---

### Task 1: Database connection + PRAGMAs

**Files:**
- Create: `server/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing test for connection PRAGMAs**

```python
# tests/test_db.py
from pathlib import Path
import pytest
from server.db import open_connection


def _pragma(conn, name: str):
    row = conn.execute(f"PRAGMA {name}").fetchone()
    return row[0] if row else None


def test_connection_has_required_pragmas(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    conn = open_connection(db_path)
    try:
        assert _pragma(conn, "journal_mode").lower() == "wal"
        # synchronous: 1 = NORMAL
        assert int(_pragma(conn, "synchronous")) == 1
        # foreign_keys: 1 = ON
        assert int(_pragma(conn, "foreign_keys")) == 1
        # busy_timeout: milliseconds, we set 5000
        assert int(_pragma(conn, "busy_timeout")) == 5000
    finally:
        conn.close()


def test_open_connection_creates_parent_dir(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "dir" / "app.db"
    conn = open_connection(db_path)
    try:
        assert db_path.parent.is_dir()
        assert db_path.exists()
    finally:
        conn.close()
```

- [ ] **Step 2: Run test — fails with ImportError**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL (ModuleNotFoundError: No module named 'server.db')

- [ ] **Step 3: Implement `server/db.py`**

```python
# server/db.py
"""SQLite connection factory. Every caller opens their own short-lived
connection; the connection is not thread-safe under concurrent writes
without per-thread isolation, which matches how FastAPI handles requests.

PRAGMAs applied on every open:
- journal_mode=WAL so readers don't block writers (SSE polling + writes).
- synchronous=NORMAL so commits don't fsync every time (crash-safe enough).
- foreign_keys=ON so CASCADE clauses in the schema actually fire.
- busy_timeout=5000 so concurrent writers wait up to 5s before raising.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def open_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn
```

- [ ] **Step 4: Run test — passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add server/db.py tests/test_db.py
git commit -m "db: open_connection factory with WAL + PRAGMAs"
```

---

### Task 2: Schema migration runner + initial SQL

**Files:**
- Create: `server/migrations/001_initial.sql`
- Modify: `server/db.py` — add `run_migrations()`
- Test: `tests/test_db.py` — add migration runner tests

- [ ] **Step 1: Write failing test for migration runner**

Append to `tests/test_db.py`:

```python
from server.db import open_connection, run_migrations


def test_run_migrations_creates_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    conn = open_connection(db_path)
    try:
        run_migrations(conn)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        # Every table defined in 001_initial.sql must exist.
        required = {
            "videos", "video_tags", "video_speakers",
            "projects", "project_videos",
            "ingests", "analyses",
            "schema_migrations",
        }
        missing = required - tables
        assert not missing, f"missing tables: {missing}"
    finally:
        conn.close()


def test_run_migrations_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    conn = open_connection(db_path)
    try:
        run_migrations(conn)
        run_migrations(conn)  # second run is a no-op
        rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
        # One row per migration file. Right now only 001_initial.
        assert [r[0] for r in rows] == ["001_initial"]
    finally:
        conn.close()


def test_foreign_keys_cascade(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    conn = open_connection(db_path)
    try:
        run_migrations(conn)
        conn.execute(
            "INSERT INTO videos(id, url, created_at, updated_at) "
            "VALUES('abc12345678', 'https://x', '2026-01-01', '2026-01-01')"
        )
        conn.execute("INSERT INTO video_tags(video_id, tag) VALUES('abc12345678', 'tag1')")
        # Deleting the video must cascade.
        conn.execute("DELETE FROM videos WHERE id='abc12345678'")
        count = conn.execute("SELECT COUNT(*) FROM video_tags").fetchone()[0]
        assert count == 0
    finally:
        conn.close()
```

- [ ] **Step 2: Run test — fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL (`run_migrations` import error; tables missing)

- [ ] **Step 3: Create `server/migrations/001_initial.sql`**

Copy this exactly — every column is load-bearing per the spec's §5 schema:

```sql
-- server/migrations/001_initial.sql
CREATE TABLE schema_migrations (
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
```

- [ ] **Step 4: Add `run_migrations()` to `server/db.py`**

Append at the end of `server/db.py`:

```python
from datetime import datetime, timezone


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_migrations(conn: sqlite3.Connection) -> list[str]:
    """Apply every `.sql` file in server/migrations/ that hasn't been
    applied yet. Ordered by filename (lexicographic). Returns the names
    that were newly applied. Idempotent."""
    # Bootstrap the tracking table. The table itself is created by the first
    # migration, but we need it to exist to read prior applied names.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        r[0] for r in conn.execute("SELECT name FROM schema_migrations")
    }

    newly: list[str] = []
    for sql_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        name = sql_path.stem
        if name in applied:
            continue
        # executescript runs the whole file as a transaction-less batch;
        # wrap it in BEGIN/COMMIT so partial failure doesn't leave tables half-made.
        conn.execute("BEGIN")
        try:
            conn.executescript(sql_path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES(?, ?)",
                (name, _iso_now()),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        newly.append(name)
    return newly
```

- [ ] **Step 5: Run tests — passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (5 tests: 2 from task 1 + 3 from task 2)

- [ ] **Step 6: Commit**

```bash
git add server/migrations/001_initial.sql server/db.py tests/test_db.py
git commit -m "db: schema migration runner + 001_initial (videos, projects, ingests, analyses)"
```

---

### Task 3: Data migration — JSON tree → SQLite

**Files:**
- Create: `server/migrate_data.py`
- Create: `tests/fixtures/migration_tree/` (fixture)
- Create: `tests/test_migrate_data.py`

- [ ] **Step 1: Build the fixture tree**

Create these files under `tests/fixtures/migration_tree/output/`:

```bash
mkdir -p tests/fixtures/migration_tree/output/aaaAAAaaa11
mkdir -p tests/fixtures/migration_tree/output/bbbBBBbbb22
```

File `tests/fixtures/migration_tree/output/aaaAAAaaa11/transcript.json`:

```json
{
  "id": "aaaAAAaaa11",
  "url": "https://youtu.be/aaaAAAaaa11",
  "title": "Diarized talk",
  "duration_sec": 1200.0,
  "language": "en",
  "language_probability": 0.98,
  "model": "distil-large-v3",
  "compute_type": "int8_float16",
  "diarized": true,
  "speaker_count": 2,
  "channel": "Channel A",
  "channel_id": "UCabc",
  "channel_url": "https://youtube.com/@a",
  "channel_follower_count": 50000,
  "upload_date": "20250101",
  "view_count": 1000,
  "like_count": 10,
  "comment_count": 2,
  "description": "A short description.",
  "categories": ["Education"],
  "yt_tags": ["ai", "research"],
  "transcription_elapsed_sec": 100.0,
  "transcription_realtime_factor": 12.0,
  "batched": true,
  "batch_size": 16,
  "segments": [
    {"id": 1, "start": 0.0, "end": 5.0, "text": "Hello.", "speaker": "SPEAKER_00"},
    {"id": 2, "start": 5.0, "end": 10.0, "text": "World.", "speaker": "SPEAKER_01"}
  ]
}
```

File `tests/fixtures/migration_tree/output/aaaAAAaaa11/meta.json`:

```json
{
  "tags": ["interview", "ai"],
  "speaker_names": {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"},
  "notes": "Worth re-listening.",
  "updated_at": "2026-01-05T10:00:00+00:00"
}
```

File `tests/fixtures/migration_tree/output/aaaAAAaaa11/analysis.json`:

```json
{
  "summary": "A summary.",
  "takeaways": ["One.", "Two."],
  "chapters": [{"start": 0, "title": "Intro"}],
  "highlights": [],
  "_meta": {"cost_usd": 0.05, "tokens_in": 500, "tokens_out": 100, "generated_at": "2026-01-05T10:30:00+00:00"}
}
```

File `tests/fixtures/migration_tree/output/aaaAAAaaa11/audio.mp3` — make a small placeholder:
```bash
printf 'not-mp3' > tests/fixtures/migration_tree/output/aaaAAAaaa11/audio.mp3
```

File `tests/fixtures/migration_tree/output/bbbBBBbbb22/transcript.json`:

```json
{
  "id": "bbbBBBbbb22",
  "url": "https://youtu.be/bbbBBBbbb22",
  "title": "Plain transcript",
  "duration_sec": 300.0,
  "language": "en",
  "diarized": false,
  "segments": [{"id": 1, "start": 0.0, "end": 3.0, "text": "Hi."}],
  "archived": true
}
```

File `tests/fixtures/migration_tree/output/_ingests.json`:

```json
[
  {
    "id": "aaaAAAaaa11",
    "url": "https://youtu.be/aaaAAAaaa11",
    "title": "Diarized talk",
    "duration_sec": 1200.0,
    "phase": "done",
    "started_at": 1735689600.0,
    "last_event_at": 1735689800.0,
    "segments": 2,
    "last_segment_end": 10.0,
    "done": true,
    "error": null,
    "cancel_requested": false
  },
  {
    "id": "ccc999PENDING",
    "url": "https://youtu.be/ccc999PENDING",
    "title": null,
    "duration_sec": null,
    "phase": "transcribing",
    "started_at": 1735689900.0,
    "last_event_at": 1735689950.0,
    "segments": 1,
    "last_segment_end": 5.0,
    "done": false,
    "error": null,
    "cancel_requested": false
  }
]
```

- [ ] **Step 2: Write failing test for data migration**

```python
# tests/test_migrate_data.py
"""Exercise the one-shot JSON → SQLite migration against a canned fixture
tree. Every assertion here maps to a requirement in the spec §11."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from server.db import open_connection, run_migrations
from server.migrate_data import migrate_data


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "migration_tree" / "output"


@pytest.fixture
def migrated_db(tmp_path: Path):
    """Copy fixture tree to tmp_path, run schema + data migration, yield conn."""
    dst = tmp_path / "output"
    shutil.copytree(FIXTURE_ROOT, dst)
    db_path = dst / "app.db"
    conn = open_connection(db_path)
    run_migrations(conn)
    stats = migrate_data(conn, output_dir=dst)
    yield conn, dst, stats
    conn.close()


def test_migrates_every_video(migrated_db) -> None:
    conn, _, stats = migrated_db
    rows = conn.execute("SELECT id, title, archived FROM videos ORDER BY id").fetchall()
    ids = [r["id"] for r in rows]
    assert ids == ["aaaAAAaaa11", "bbbBBBbbb22"]
    # Archived flag round-trips.
    archived = {r["id"]: r["archived"] for r in rows}
    assert archived["aaaAAAaaa11"] == 0
    assert archived["bbbBBBbbb22"] == 1
    assert stats["videos_migrated"] == 2


def test_migrates_all_video_columns(migrated_db) -> None:
    conn, _, _ = migrated_db
    row = conn.execute(
        "SELECT * FROM videos WHERE id='aaaAAAaaa11'"
    ).fetchone()
    assert row["title"] == "Diarized talk"
    assert row["channel"] == "Channel A"
    assert row["channel_follower_count"] == 50000
    assert row["upload_date"] == "20250101"
    assert row["language_probability"] == pytest.approx(0.98)
    assert row["diarized"] == 1
    assert row["speaker_count"] == 2
    assert row["model"] == "distil-large-v3"
    assert row["batched"] == 1
    assert row["batch_size"] == 16
    assert row["transcription_realtime_factor"] == pytest.approx(12.0)
    assert row["segment_count"] == 2
    # JSON-encoded list columns.
    import json
    assert json.loads(row["yt_tags"]) == ["ai", "research"]
    assert json.loads(row["categories"]) == ["Education"]


def test_migrates_tags_speakers_notes(migrated_db) -> None:
    conn, _, _ = migrated_db
    tags = {r["tag"] for r in conn.execute(
        "SELECT tag FROM video_tags WHERE video_id='aaaAAAaaa11'"
    )}
    assert tags == {"interview", "ai"}

    speakers = {
        r["label"]: r["name"] for r in conn.execute(
            "SELECT label, name FROM video_speakers WHERE video_id='aaaAAAaaa11'"
        )
    }
    assert speakers == {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}

    notes = conn.execute(
        "SELECT notes FROM videos WHERE id='aaaAAAaaa11'"
    ).fetchone()["notes"]
    assert notes == "Worth re-listening."


def test_migrates_analysis_row_and_moves_file(migrated_db) -> None:
    conn, out_dir, _ = migrated_db
    rows = conn.execute(
        "SELECT provider, model, status, cost_usd, tokens_in, tokens_out, file_path "
        "FROM analyses WHERE video_id='aaaAAAaaa11'"
    ).fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["provider"] == "claude_cli"
    assert r["model"] == "legacy"
    assert r["status"] == "done"
    assert r["cost_usd"] == pytest.approx(0.05)
    assert r["tokens_in"] == 500
    assert r["tokens_out"] == 100
    # file_path points at the archived copy.
    archived_path = out_dir / "aaaAAAaaa11" / r["file_path"]
    assert archived_path.exists()
    # Original analysis.json either still exists (symlink) or is gone (if a
    # plain copy was made). Either way the content must be reachable via the
    # path stored in file_path.
    import json
    data = json.loads(archived_path.read_text())
    assert data["summary"] == "A summary."


def test_orphans_nondone_ingests(migrated_db) -> None:
    conn, _, _ = migrated_db
    rows = conn.execute(
        "SELECT video_id, done, error FROM ingests ORDER BY video_id"
    ).fetchall()
    assert len(rows) == 2
    by_vid = {r["video_id"]: r for r in rows}
    assert by_vid["aaaAAAaaa11"]["done"] == 1
    # Non-done row flipped to done with orphan error.
    assert by_vid["ccc999PENDING"]["done"] == 1
    assert by_vid["ccc999PENDING"]["error"] is not None


def test_computes_storage_bytes(migrated_db) -> None:
    conn, _, _ = migrated_db
    row = conn.execute(
        "SELECT storage_bytes FROM videos WHERE id='aaaAAAaaa11'"
    ).fetchone()
    assert row["storage_bytes"] > 0  # sum of all files in the folder


def test_migration_is_idempotent(migrated_db) -> None:
    conn, out_dir, _ = migrated_db
    # Second run: count stays the same; no duplicate rows.
    stats2 = migrate_data(conn, output_dir=out_dir)
    count = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    assert count == 2
    assert stats2["videos_migrated"] == 0  # nothing new to migrate


def test_source_files_not_deleted(migrated_db) -> None:
    _, out_dir, _ = migrated_db
    # meta.json + original transcript.json + _ingests.json untouched.
    assert (out_dir / "aaaAAAaaa11" / "meta.json").exists()
    assert (out_dir / "aaaAAAaaa11" / "transcript.json").exists()
    assert (out_dir / "_ingests.json").exists()
```

- [ ] **Step 3: Run tests — fails**

Run: `uv run pytest tests/test_migrate_data.py -v`
Expected: FAIL (no module `server.migrate_data`)

- [ ] **Step 4: Implement `server/migrate_data.py`**

```python
# server/migrate_data.py
"""One-shot migration: walks output/<video_id>/ folders and _ingests.json,
populates the SQLite DB. Idempotent — safe to call on every startup.

Does NOT delete or modify source files. Flipping an existing analysis.json
into analyses/1.json is the one disk mutation (with a symlink or copy
fallback) so multiple analysis runs can coexist later.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import run_migrations

log = logging.getLogger(__name__)

_MARKER_KEY = "data_migration_v1"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _folder_bytes(folder: Path) -> int:
    total = 0
    for p in folder.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _as_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _upsert_video(conn, out_dir: Path, video_id: str, tj: dict, meta: dict, archived: bool) -> bool:
    """Insert/update videos row. Returns True if a new row was inserted."""
    existing = conn.execute(
        "SELECT id FROM videos WHERE id = ?", (video_id,)
    ).fetchone()
    if existing is not None:
        return False

    folder = out_dir / video_id
    now = _iso_now()
    conn.execute(
        """
        INSERT INTO videos (
            id, url, title, channel, channel_id, channel_url,
            channel_follower_count, upload_date, duration_sec, description,
            categories, yt_tags, view_count, like_count, comment_count,
            language, language_probability, diarized, speaker_count,
            segment_count, model, compute_type, batched, batch_size,
            transcription_elapsed_sec, transcription_realtime_factor,
            storage_bytes, archived, notes, owner, transcribed_at,
            created_at, updated_at
        ) VALUES (
            :id, :url, :title, :channel, :channel_id, :channel_url,
            :channel_follower_count, :upload_date, :duration_sec, :description,
            :categories, :yt_tags, :view_count, :like_count, :comment_count,
            :language, :language_probability, :diarized, :speaker_count,
            :segment_count, :model, :compute_type, :batched, :batch_size,
            :transcription_elapsed_sec, :transcription_realtime_factor,
            :storage_bytes, :archived, :notes, :owner, :transcribed_at,
            :created_at, :updated_at
        )
        """,
        {
            "id": video_id,
            "url": tj.get("url") or f"https://youtu.be/{video_id}",
            "title": tj.get("title"),
            "channel": tj.get("channel"),
            "channel_id": tj.get("channel_id"),
            "channel_url": tj.get("channel_url"),
            "channel_follower_count": tj.get("channel_follower_count"),
            "upload_date": tj.get("upload_date"),
            "duration_sec": tj.get("duration_sec"),
            "description": tj.get("description"),
            "categories": _as_json(tj.get("categories")),
            "yt_tags": _as_json(tj.get("yt_tags")),
            "view_count": tj.get("view_count"),
            "like_count": tj.get("like_count"),
            "comment_count": tj.get("comment_count"),
            "language": tj.get("language"),
            "language_probability": tj.get("language_probability"),
            "diarized": 1 if tj.get("diarized") else 0,
            "speaker_count": tj.get("speaker_count"),
            "segment_count": len(tj.get("segments") or []),
            "model": tj.get("model"),
            "compute_type": tj.get("compute_type"),
            "batched": (1 if tj.get("batched") else 0) if tj.get("batched") is not None else None,
            "batch_size": tj.get("batch_size"),
            "transcription_elapsed_sec": tj.get("transcription_elapsed_sec"),
            "transcription_realtime_factor": tj.get("transcription_realtime_factor"),
            "storage_bytes": _folder_bytes(folder),
            "archived": 1 if archived else 0,
            "notes": meta.get("notes") or None,
            "owner": None,
            "transcribed_at": None,
            "created_at": now,
            "updated_at": now,
        },
    )
    return True


def _insert_tags(conn, video_id: str, tags: list[str]) -> None:
    if not tags:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO video_tags(video_id, tag) VALUES(?, ?)",
        [(video_id, t) for t in tags if t],
    )


def _insert_speakers(conn, video_id: str, speakers: dict[str, str]) -> None:
    if not speakers:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO video_speakers(video_id, label, name) VALUES(?, ?, ?)",
        [(video_id, label, name) for label, name in speakers.items() if name],
    )


def _migrate_analysis(conn, folder: Path, video_id: str) -> bool:
    """If an analysis.json exists and no analyses row is recorded yet,
    create one row and move the file into analyses/<id>.json so future runs
    can accumulate history. Returns True on first-time migration."""
    src = folder / "analysis.json"
    if not src.exists():
        return False
    existing = conn.execute(
        "SELECT id FROM analyses WHERE video_id = ?", (video_id,)
    ).fetchone()
    if existing is not None:
        return False

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        log.warning("could not parse analysis.json for %s; skipping", video_id)
        return False

    m = data.get("_meta") or {}
    cur = conn.execute(
        """
        INSERT INTO analyses (
            video_id, provider, model, status, started_at, finished_at,
            cost_usd, tokens_in, tokens_out, error, file_path
        ) VALUES (?, 'claude_cli', 'legacy', 'done', ?, ?, ?, ?, ?, NULL, NULL)
        """,
        (
            video_id,
            m.get("generated_at") or _iso_now(),
            m.get("generated_at"),
            m.get("cost_usd"),
            m.get("tokens_in"),
            m.get("tokens_out"),
        ),
    )
    analysis_id = cur.lastrowid
    target_dir = folder / "analyses"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / f"{analysis_id}.json"
    shutil.copy2(src, target)

    # Replace the top-level analysis.json with a symlink. Windows: if symlink
    # isn't allowed (no admin, no Developer Mode), fall back to keeping the
    # copy at the original location — the DB path still leads to the truth.
    try:
        src.unlink()
        os.symlink(target.name, src, target_is_directory=False)
    except (OSError, NotImplementedError) as e:
        log.warning(
            "symlink unavailable (%s); leaving analysis.json as plain copy",
            e,
        )
        # If unlinked but symlink failed, restore the content via copy-back.
        if not src.exists():
            shutil.copy2(target, src)

    conn.execute(
        "UPDATE analyses SET file_path = ? WHERE id = ?",
        (f"analyses/{analysis_id}.json", analysis_id),
    )
    return True


def _migrate_ingests(conn, out_dir: Path) -> int:
    """Load output/_ingests.json and upsert into ingests. Non-done rows flip
    to done with an orphan error; the original workers are gone."""
    src = out_dir / "_ingests.json"
    if not src.exists():
        return 0
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        log.warning("could not parse _ingests.json; skipping")
        return 0

    already = {
        r["video_id"] for r in conn.execute("SELECT video_id FROM ingests")
    }
    inserted = 0
    for row in data:
        vid = row.get("id")
        if not vid or vid in already:
            continue
        done = bool(row.get("done"))
        err = row.get("error")
        if not done:
            err = err or "orphaned (pre-migration)"
            done = True
        conn.execute(
            """
            INSERT INTO ingests (
                video_id, url, title, duration_sec, phase,
                started_at, last_event_at, segments, last_segment_end,
                done, error, cancel_requested, project_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                vid,
                row.get("url"),
                row.get("title"),
                row.get("duration_sec"),
                row.get("phase") or "orphaned",
                row.get("started_at") or 0,
                row.get("last_event_at") or 0,
                row.get("segments") or 0,
                row.get("last_segment_end") or 0,
                1 if done else 0,
                err,
                1 if row.get("cancel_requested") else 0,
            ),
        )
        inserted += 1
    return inserted


def migrate_data(conn, output_dir: Path) -> dict[str, int]:
    """Walk output/ and populate SQLite. Idempotent. Returns counts."""
    # Ensure schema is present (callers should have run_migrations; guard anyway).
    run_migrations(conn)

    videos_migrated = 0
    analyses_migrated = 0

    if not output_dir.exists():
        return {
            "videos_migrated": 0,
            "analyses_migrated": 0,
            "ingests_migrated": 0,
        }

    conn.execute("BEGIN")
    try:
        for sub in sorted(output_dir.iterdir()):
            if not sub.is_dir():
                continue
            tj_path = sub / "transcript.json"
            if not tj_path.exists():
                continue
            try:
                tj = json.loads(tj_path.read_text(encoding="utf-8"))
            except Exception:
                log.warning("skipping %s: bad transcript.json", sub.name)
                continue

            meta_path = sub / "meta.json"
            meta = {"tags": [], "speaker_names": {}, "notes": ""}
            if meta_path.exists():
                try:
                    loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        meta.update({
                            "tags": loaded.get("tags") or [],
                            "speaker_names": loaded.get("speaker_names") or {},
                            "notes": loaded.get("notes") or "",
                        })
                except Exception:
                    pass

            archived = bool(tj.get("archived"))
            video_id = tj.get("id") or sub.name
            inserted = _upsert_video(
                conn, output_dir, video_id, tj, meta, archived
            )
            if inserted:
                videos_migrated += 1
                _insert_tags(conn, video_id, meta.get("tags") or [])
                _insert_speakers(conn, video_id, meta.get("speaker_names") or {})
            # Analysis migration is its own path — run even when the video
            # row existed already, in case a previous partial migration
            # left it out.
            if _migrate_analysis(conn, sub, video_id):
                analyses_migrated += 1

        ingests_migrated = _migrate_ingests(conn, output_dir)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {
        "videos_migrated": videos_migrated,
        "analyses_migrated": analyses_migrated,
        "ingests_migrated": ingests_migrated,
    }
```

- [ ] **Step 5: Run tests — passes**

Run: `uv run pytest tests/test_migrate_data.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
git add server/migrate_data.py tests/test_migrate_data.py tests/fixtures/migration_tree/
git commit -m "db: one-shot JSON → SQLite data migration (idempotent)"
```

---

### Task 4: Videos repository — read/write via DB

Replaces `transcripts.py`'s filesystem-scan internals with DB queries.

**Files:**
- Modify: `server/transcripts.py` — switch internals to DB; keep public function signatures
- Create: `tests/test_videos_db.py`

- [ ] **Step 1: Add a videos repo test**

```python
# tests/test_videos_db.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from server.db import open_connection, run_migrations
from server import transcripts


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def conn(tmp_path: Path):
    db_path = tmp_path / "app.db"
    c = open_connection(db_path)
    run_migrations(c)
    yield c
    c.close()


def _seed_video(conn, vid: str, *, archived: int = 0, title: str = "t") -> None:
    now = _iso_now()
    conn.execute(
        "INSERT INTO videos(id, url, title, archived, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (vid, f"https://youtu.be/{vid}", title, archived, now, now),
    )


def test_list_transcripts_excludes_archived(tmp_path: Path, conn) -> None:
    _seed_video(conn, "live000abcde", archived=0, title="Live")
    _seed_video(conn, "arch0000abcd", archived=1, title="Archived")
    out = transcripts.list_transcripts(tmp_path, conn=conn)
    ids = [r["id"] for r in out]
    assert ids == ["live000abcde"]
    # Sorted most-recent first via created_at.
    assert out[0]["title"] == "Live"


def test_list_archived_returns_only_archived(tmp_path: Path, conn) -> None:
    _seed_video(conn, "live000abcde", archived=0)
    _seed_video(conn, "arch0000abcd", archived=1)
    out = transcripts.list_archived(tmp_path, conn=conn)
    assert [r["id"] for r in out] == ["arch0000abcd"]


def test_set_archived_flips_flag(tmp_path: Path, conn) -> None:
    _seed_video(conn, "live000abcde")
    assert transcripts.set_archived(tmp_path, "live000abcde", True, conn=conn)
    row = conn.execute("SELECT archived FROM videos WHERE id='live000abcde'").fetchone()
    assert row["archived"] == 1


def test_set_archived_returns_false_for_unknown(tmp_path: Path, conn) -> None:
    assert transcripts.set_archived(tmp_path, "nonexistent", True, conn=conn) is False


def test_read_transcript_prefers_disk(tmp_path: Path, conn) -> None:
    """Segments are still canonical on disk; read_transcript merges the DB
    row metadata with the on-disk segments."""
    video_id = "live000abcde"
    folder = tmp_path / video_id
    folder.mkdir()
    (folder / "transcript.json").write_text(json.dumps({
        "id": video_id, "url": "https://x",
        "title": "On disk", "segments": [{"id": 1, "start": 0, "end": 1, "text": "hi"}],
    }), encoding="utf-8")
    _seed_video(conn, video_id, title="Also in DB")
    r = transcripts.read_transcript(tmp_path, video_id, conn=conn)
    assert r is not None
    # Segments come from disk.
    assert len(r["segments"]) == 1
    assert r["segments"][0]["text"] == "hi"


def test_delete_video_removes_row_and_folder(tmp_path: Path, conn) -> None:
    video_id = "live000abcde"
    folder = tmp_path / video_id
    folder.mkdir()
    (folder / "transcript.json").write_text("{}", encoding="utf-8")
    _seed_video(conn, video_id, archived=1)
    assert transcripts.delete_video(tmp_path, video_id, conn=conn)
    # DB row gone.
    assert conn.execute(
        "SELECT 1 FROM videos WHERE id=?", (video_id,)
    ).fetchone() is None
    # Folder gone.
    assert not folder.exists()
```

- [ ] **Step 2: Run tests — fails**

Run: `uv run pytest tests/test_videos_db.py -v`
Expected: FAIL (list_transcripts doesn't take `conn` kwarg; scans disk instead)

- [ ] **Step 3: Replace `server/transcripts.py` internals**

Rewrite `server/transcripts.py` entirely:

```python
# server/transcripts.py
"""Videos repository. Canonical source is the `videos` table; segment-level
data (too big for a row) stays on disk in output/<id>/transcript.json.

Public function signatures match the pre-Slice-1 shapes so routers don't
change, but internals are now DB-backed. The optional `conn` kwarg is for
tests; production callers pass the live DB connection via the server.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .layout import video_dir, transcript_json


# --- connection plumbing --------------------------------------------------

def _ensure_conn(conn: sqlite3.Connection | None, out_dir: Path) -> tuple[sqlite3.Connection, bool]:
    """If caller didn't pass a conn, open one against output/app.db.
    The bool is True when we own the connection (and should close it)."""
    if conn is not None:
        return conn, False
    from .db import open_connection
    return open_connection(out_dir / "app.db"), True


def _close_if_owned(conn: sqlite3.Connection, owned: bool) -> None:
    if owned:
        conn.close()


# --- row → summary dict ---------------------------------------------------

_SUMMARY_COLS = [
    "id", "title", "duration_sec", "language", "diarized",
    "speaker_count", "model", "segment_count",
    "archived", "channel", "channel_url", "upload_date",
    "view_count", "like_count",
]


def _summary_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    out = {k: row[k] for k in _SUMMARY_COLS}
    out["archived"] = bool(out["archived"])
    out["diarized"] = bool(out["diarized"])
    # Tags: read from video_tags table.
    out["tags"] = [
        r["tag"] for r in conn.execute(
            "SELECT tag FROM video_tags WHERE video_id=? ORDER BY tag",
            (row["id"],),
        )
    ]
    return out


def _list_by_archived(
    conn: sqlite3.Connection, archived: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM videos WHERE archived=? ORDER BY created_at DESC",
        (archived,),
    ).fetchall()
    return [_summary_from_row(conn, r) for r in rows]


# --- public API -----------------------------------------------------------

def list_transcripts(
    out_dir: Path, conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        return _list_by_archived(c, 0)
    finally:
        _close_if_owned(c, owned)


def list_archived(
    out_dir: Path, conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        return _list_by_archived(c, 1)
    finally:
        _close_if_owned(c, owned)


def read_transcript(
    out_dir: Path, video_id: str, conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    """Reads the on-disk transcript.json (authoritative for segments)
    and layers the DB row on top for any DB-only fields. Returns None
    when the video is unknown."""
    p = transcript_json(out_dir, video_id)
    disk: dict[str, Any] | None = None
    if p.exists():
        try:
            disk = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            disk = None

    c, owned = _ensure_conn(conn, out_dir)
    try:
        row = c.execute(
            "SELECT * FROM videos WHERE id=?", (video_id,)
        ).fetchone()
        if row is None and disk is None:
            return None
        base: dict[str, Any] = dict(row) if row else {}
        if disk:
            # Disk wins for segments; DB fills in indexed columns.
            base.update({k: v for k, v in disk.items() if k != "archived"})
            if row is not None:
                base["archived"] = bool(row["archived"])
        return base
    finally:
        _close_if_owned(c, owned)


def set_archived(
    out_dir: Path,
    video_id: str,
    archived: bool,
    conn: sqlite3.Connection | None = None,
) -> bool:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        cur = c.execute(
            "UPDATE videos SET archived=?, updated_at=datetime('now') WHERE id=?",
            (1 if archived else 0, video_id),
        )
        return cur.rowcount > 0
    finally:
        _close_if_owned(c, owned)


def delete_video(
    out_dir: Path, video_id: str, conn: sqlite3.Connection | None = None,
) -> bool:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        cur = c.execute("DELETE FROM videos WHERE id=?", (video_id,))
        if cur.rowcount == 0:
            return False
    finally:
        _close_if_owned(c, owned)
    vd = video_dir(out_dir, video_id)
    if vd.exists():
        shutil.rmtree(vd, ignore_errors=False)
    return True
```

- [ ] **Step 4: Run tests — should pass**

Run: `uv run pytest tests/test_videos_db.py tests/test_migrate_data.py -v`
Expected: PASS (6 + 8 tests). If migration test now fails, the assertion about `_summary_from_row` schema may have diverged — fix inline.

- [ ] **Step 5: Commit**

```bash
git add server/transcripts.py tests/test_videos_db.py
git commit -m "transcripts: DB-backed list/read/archive/delete; segments stay on disk"
```

---

### Task 5: Meta (tags / speakers / notes) via DB

**Files:**
- Modify: `server/meta.py` — internals → DB
- Create: `tests/test_meta_db.py`

- [ ] **Step 1: Write failing meta tests**

```python
# tests/test_meta_db.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from server.db import open_connection, run_migrations
from server import meta


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def conn(tmp_path: Path):
    db_path = tmp_path / "app.db"
    c = open_connection(db_path)
    run_migrations(c)
    now = _iso_now()
    c.execute(
        "INSERT INTO videos(id, url, created_at, updated_at) VALUES(?, ?, ?, ?)",
        ("abc12345678", "https://youtu.be/abc12345678", now, now),
    )
    yield c
    c.close()


def test_read_empty_meta(tmp_path: Path, conn) -> None:
    r = meta.read_meta(tmp_path, "abc12345678", conn=conn)
    assert r == {"tags": [], "speaker_names": {}, "notes": "", "updated_at": None}


def test_write_then_read_meta(tmp_path: Path, conn) -> None:
    meta.write_meta(
        tmp_path, "abc12345678",
        {"tags": ["interview", "ai"], "notes": "Great."},
        conn=conn,
    )
    r = meta.read_meta(tmp_path, "abc12345678", conn=conn)
    assert r["tags"] == ["interview", "ai"]
    assert r["notes"] == "Great."
    assert r["updated_at"] is not None


def test_tags_dedupe_and_strip(tmp_path: Path, conn) -> None:
    meta.write_meta(
        tmp_path, "abc12345678",
        {"tags": [" Ai ", "ai", "Interview"]},
        conn=conn,
    )
    r = meta.read_meta(tmp_path, "abc12345678", conn=conn)
    # Dedupe is case-insensitive; preserves first-seen casing.
    assert r["tags"] == ["Ai", "Interview"]


def test_speaker_names_round_trip(tmp_path: Path, conn) -> None:
    meta.write_meta(
        tmp_path, "abc12345678",
        {"speaker_names": {"SPEAKER_00": "Alice", "SPEAKER_01": "  "}},
        conn=conn,
    )
    r = meta.read_meta(tmp_path, "abc12345678", conn=conn)
    # Empty names dropped.
    assert r["speaker_names"] == {"SPEAKER_00": "Alice"}


def test_notes_only_update_preserves_tags(tmp_path: Path, conn) -> None:
    meta.write_meta(tmp_path, "abc12345678", {"tags": ["a"]}, conn=conn)
    meta.write_meta(tmp_path, "abc12345678", {"notes": "hi"}, conn=conn)
    r = meta.read_meta(tmp_path, "abc12345678", conn=conn)
    assert r["tags"] == ["a"]
    assert r["notes"] == "hi"
```

- [ ] **Step 2: Run tests — fails**

Run: `uv run pytest tests/test_meta_db.py -v`
Expected: FAIL (meta.py signatures don't accept `conn`, or tags not persisted in DB tables)

- [ ] **Step 3: Replace `server/meta.py`**

```python
# server/meta.py
"""User-editable per-video metadata: tags, speaker renames, free-form notes.

Backed by the `video_tags`, `video_speakers`, and `videos.notes` columns.
Separate from the on-disk transcript.json so re-ingest never clobbers user edits.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_conn(conn, out_dir: Path):
    if conn is not None:
        return conn, False
    from .db import open_connection
    return open_connection(out_dir / "app.db"), True


def _close_if_owned(conn, owned: bool) -> None:
    if owned:
        conn.close()


def _empty() -> dict[str, Any]:
    return {"tags": [], "speaker_names": {}, "notes": "", "updated_at": None}


def read_meta(
    out_dir: Path, video_id: str, conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        row = c.execute(
            "SELECT notes, updated_at FROM videos WHERE id=?", (video_id,)
        ).fetchone()
        if row is None:
            return _empty()
        tags = [
            r["tag"] for r in c.execute(
                "SELECT tag FROM video_tags WHERE video_id=? ORDER BY tag",
                (video_id,),
            )
        ]
        speakers = {
            r["label"]: r["name"] for r in c.execute(
                "SELECT label, name FROM video_speakers WHERE video_id=?",
                (video_id,),
            )
        }
        return {
            "tags": tags,
            "speaker_names": speakers,
            "notes": row["notes"] or "",
            "updated_at": row["updated_at"],
        }
    finally:
        _close_if_owned(c, owned)


def _dedupe_tags(raw: list) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        s = str(t).strip()
        if not s:
            continue
        low = s.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(s)
    return out


def write_meta(
    out_dir: Path,
    video_id: str,
    updates: dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Merge `updates` into the video's meta tables. Returns the merged
    record. Unknown keys are ignored. Only the fields actually present in
    `updates` are modified — omitted keys preserve prior values."""
    c, owned = _ensure_conn(conn, out_dir)
    try:
        c.execute("BEGIN")
        try:
            # The video row must exist (the ingest pipeline creates it).
            exists = c.execute(
                "SELECT 1 FROM videos WHERE id=?", (video_id,)
            ).fetchone()
            if exists is None:
                c.execute("ROLLBACK")
                return _empty()

            if "tags" in updates and isinstance(updates["tags"], list):
                cleaned = _dedupe_tags(updates["tags"])
                c.execute(
                    "DELETE FROM video_tags WHERE video_id=?", (video_id,)
                )
                if cleaned:
                    c.executemany(
                        "INSERT INTO video_tags(video_id, tag) VALUES(?, ?)",
                        [(video_id, t) for t in cleaned],
                    )
            if "speaker_names" in updates and isinstance(updates["speaker_names"], dict):
                sn = {
                    str(k): str(v).strip()
                    for k, v in updates["speaker_names"].items()
                    if str(v).strip()
                }
                c.execute(
                    "DELETE FROM video_speakers WHERE video_id=?", (video_id,)
                )
                if sn:
                    c.executemany(
                        "INSERT INTO video_speakers(video_id, label, name) VALUES(?, ?, ?)",
                        [(video_id, lbl, name) for lbl, name in sn.items()],
                    )
            if "notes" in updates and isinstance(updates["notes"], str):
                c.execute(
                    "UPDATE videos SET notes=? WHERE id=?",
                    (updates["notes"], video_id),
                )
            c.execute(
                "UPDATE videos SET updated_at=? WHERE id=?",
                (_iso_now(), video_id),
            )
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        return read_meta(out_dir, video_id, conn=c)
    finally:
        _close_if_owned(c, owned)
```

- [ ] **Step 4: Run tests — pass**

Run: `uv run pytest tests/test_meta_db.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add server/meta.py tests/test_meta_db.py
git commit -m "meta: DB-backed tags / speakers / notes via video_tags, video_speakers"
```

---

### Task 6: Ingest pipeline upserts videos row on done

Key integration: when transcription finishes, the DB must learn about the new video. Otherwise `list_transcripts` (which now reads from DB) returns stale.

**Files:**
- Modify: `server/transcriber.py` — add DB upsert at the end of `write_outputs` (or right after it)
- Modify: `server/main.py` — pass DB conn into transcribe worker
- Create: `tests/test_ingest_writes_db.py`

- [ ] **Step 1: Decide the seam — wrap `write_outputs`**

The simplest seam: add a new helper `persist_video_to_db()` in `server/transcriber.py` that inserts a videos row from the same `meta` dict that `write_outputs` computes. Called right after `write_outputs` returns, **before** the pipeline emits `done`.

- [ ] **Step 2: Write failing test**

```python
# tests/test_ingest_writes_db.py
"""After ingest, the videos table has a row with the right metadata.
Mirrors the Slice-0 mocking harness but asserts DB state."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import server.transcriber as tr
from server import state as state_mod
from server.db import open_connection, run_migrations


@pytest.fixture(autouse=True)
def _clean_state():
    state_mod._INGESTS.clear()
    old = state_mod._PERSIST_PATH
    state_mod._PERSIST_PATH = None
    yield
    state_mod._INGESTS.clear()
    state_mod._PERSIST_PATH = old


def test_ingest_inserts_videos_row(tmp_path: Path) -> None:
    out_dir = tmp_path
    video_id = "abcXYZ11111"
    folder = out_dir / video_id
    folder.mkdir()
    (folder / "audio.mp3").write_bytes(b"fake")
    (folder / "transcript.json").write_text("{}", encoding="utf-8")
    (folder / "transcript.txt").write_text("", encoding="utf-8")
    (folder / "transcript.srt").write_text("", encoding="utf-8")
    (folder / "CLAUDE.md").write_text("", encoding="utf-8")

    # Prime the DB schema.
    conn = open_connection(out_dir / "app.db")
    run_migrations(conn)
    conn.close()

    fake_info = {
        "id": video_id, "title": "Hello", "duration": 60,
        "channel": "Ch", "description": "d",
    }
    fake_result = {
        "segments": [{"id": 1, "start": 0, "end": 1, "text": "hi"}],
        "language": "en", "language_probability": 0.9,
        "elapsed_sec": 5.0, "diarized": False,
    }
    fake_files = {k: folder / f for k, f in [
        ("txt", "transcript.txt"),
        ("srt", "transcript.srt"),
        ("json", "transcript.json"),
        ("claude_md", "CLAUDE.md"),
    ]}

    with (
        patch.object(tr, "download_audio", return_value=(folder / "audio.mp3", fake_info)),
        patch.object(tr, "_run_plain", return_value=fake_result),
        patch.object(tr, "_run_post_diarize", return_value=False),
        patch.object(tr, "write_outputs", return_value=fake_files),
    ):
        req = tr.TranscribeRequest(url=f"https://youtu.be/{video_id}", device="cpu")
        events = asyncio.run(_drain(tr.stream_transcription(req, out_dir, hf_token=None)))

    assert any(e["event"] == "done" for e in events)
    conn = open_connection(out_dir / "app.db")
    try:
        row = conn.execute(
            "SELECT id, title, segment_count, language FROM videos WHERE id=?",
            (video_id,),
        ).fetchone()
        assert row is not None
        assert row["title"] == "Hello"
        assert row["segment_count"] == 1
        assert row["language"] == "en"
    finally:
        conn.close()


async def _drain(gen):
    out = []
    async for evt in gen:
        out.append(evt)
    return out
```

- [ ] **Step 3: Run test — fails**

Run: `uv run pytest tests/test_ingest_writes_db.py -v`
Expected: FAIL (no videos row after ingest)

- [ ] **Step 4: Add `persist_video_to_db` and call it**

Add to the END of `server/transcriber.py`:

```python
from datetime import datetime, timezone


def _folder_bytes(folder: Path) -> int:
    total = 0
    for p in folder.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def persist_video_to_db(
    out_dir: Path,
    video_id: str,
    info: dict,
    result: dict,
    req: "TranscribeRequest",
) -> None:
    """Upsert a row in `videos` from the transcription outputs. Called
    right after write_outputs; failures here are logged but don't fail
    the stream (user still has their disk artifacts)."""
    import json as _json
    import logging
    log = logging.getLogger("server.transcriber.db")
    try:
        from .db import open_connection, run_migrations
        conn = open_connection(out_dir / "app.db")
    except Exception as e:
        log.warning("could not open DB to persist %s: %s", video_id, e)
        return
    try:
        run_migrations(conn)
        now = datetime.now(timezone.utc).isoformat()
        segments = result.get("segments") or []
        row = {
            "id": video_id,
            "url": req.url,
            "title": info.get("title"),
            "channel": info.get("channel") or info.get("uploader"),
            "channel_id": info.get("channel_id") or info.get("uploader_id"),
            "channel_url": info.get("channel_url") or info.get("uploader_url"),
            "channel_follower_count": info.get("channel_follower_count"),
            "upload_date": info.get("upload_date"),
            "duration_sec": info.get("duration"),
            "description": (info.get("description") or "")[:1000] or None,
            "categories": _json.dumps(info.get("categories") or []),
            "yt_tags": _json.dumps(info.get("tags") or []),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "comment_count": info.get("comment_count"),
            "language": result.get("language"),
            "language_probability": result.get("language_probability"),
            "diarized": 1 if result.get("diarized") else 0,
            "speaker_count": len({s.get("speaker") for s in segments if s.get("speaker")}),
            "segment_count": len(segments),
            "model": req.model,
            "compute_type": req.compute_type,
            "batched": 1 if req.batched else 0,
            "batch_size": req.batch_size,
            "transcription_elapsed_sec": result.get("elapsed_sec"),
            "transcription_realtime_factor": (
                (info.get("duration") or 0) / result["elapsed_sec"]
                if result.get("elapsed_sec") else None
            ),
            "storage_bytes": _folder_bytes(out_dir / video_id),
            "transcribed_at": now,
            "updated_at": now,
        }
        # Upsert: ON CONFLICT means re-ingest updates in place, preserving
        # user-edited fields (archived, notes, owner, created_at).
        conn.execute("""
            INSERT INTO videos(
                id, url, title, channel, channel_id, channel_url,
                channel_follower_count, upload_date, duration_sec, description,
                categories, yt_tags, view_count, like_count, comment_count,
                language, language_probability, diarized, speaker_count,
                segment_count, model, compute_type, batched, batch_size,
                transcription_elapsed_sec, transcription_realtime_factor,
                storage_bytes, archived, notes, owner, transcribed_at,
                created_at, updated_at
            ) VALUES (
                :id, :url, :title, :channel, :channel_id, :channel_url,
                :channel_follower_count, :upload_date, :duration_sec, :description,
                :categories, :yt_tags, :view_count, :like_count, :comment_count,
                :language, :language_probability, :diarized, :speaker_count,
                :segment_count, :model, :compute_type, :batched, :batch_size,
                :transcription_elapsed_sec, :transcription_realtime_factor,
                :storage_bytes, 0, NULL, NULL, :transcribed_at,
                :updated_at, :updated_at
            )
            ON CONFLICT(id) DO UPDATE SET
                url=excluded.url,
                title=excluded.title,
                channel=excluded.channel,
                channel_id=excluded.channel_id,
                channel_url=excluded.channel_url,
                channel_follower_count=excluded.channel_follower_count,
                upload_date=excluded.upload_date,
                duration_sec=excluded.duration_sec,
                description=excluded.description,
                categories=excluded.categories,
                yt_tags=excluded.yt_tags,
                view_count=excluded.view_count,
                like_count=excluded.like_count,
                comment_count=excluded.comment_count,
                language=excluded.language,
                language_probability=excluded.language_probability,
                diarized=excluded.diarized,
                speaker_count=excluded.speaker_count,
                segment_count=excluded.segment_count,
                model=excluded.model,
                compute_type=excluded.compute_type,
                batched=excluded.batched,
                batch_size=excluded.batch_size,
                transcription_elapsed_sec=excluded.transcription_elapsed_sec,
                transcription_realtime_factor=excluded.transcription_realtime_factor,
                storage_bytes=excluded.storage_bytes,
                transcribed_at=excluded.transcribed_at,
                updated_at=excluded.updated_at
        """, row)
    except Exception as e:
        log.warning("persist_video_to_db failed for %s: %s", video_id, e)
    finally:
        try: conn.close()
        except Exception: pass
```

Then INSIDE `stream_transcription`'s worker, right after the line `files = write_outputs(...)`, add:

```python
            persist_video_to_db(out_dir, video_id, info, result, req)
```

(Literally insert that line between `files = write_outputs(...)` and the `duration = info.get("duration") or 0` line.)

- [ ] **Step 5: Run tests — pass**

Run: `uv run pytest tests/test_ingest_writes_db.py tests/test_analyze_trigger.py -v`
Expected: PASS (1 new + 2 from Slice 0 still green)

- [ ] **Step 6: Commit**

```bash
git add server/transcriber.py tests/test_ingest_writes_db.py
git commit -m "transcriber: upsert videos row at ingest-done (DB-backed library)"
```

---

### Task 7: Projects service + API routes

**Files:**
- Create: `server/projects.py` — CRUD + membership + slug
- Modify: `server/main.py` — wire routes + run migrations at startup
- Create: `tests/test_projects_api.py`

- [ ] **Step 1: Write failing API tests**

```python
# tests/test_projects_api.py
"""Projects CRUD + membership via HTTP. Uses httpx.AsyncClient against a
fresh FastAPI app backed by a tmp_path output dir."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    # Point the server at a tmp output dir and disable auth.
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("AUTH_DISABLED", "1")
    # Fresh import so module-level OUTPUT_DIR picks up env.
    import importlib
    import server.main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_empty_projects_list(client) -> None:
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert r.json() == []


def test_create_project(client) -> None:
    r = client.post("/api/projects", json={"name": "My Project", "description": "D"})
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["id"] == "my-project"
    assert data["name"] == "My Project"
    assert data["video_count"] == 0


def test_slug_collision_autosuffix(client) -> None:
    client.post("/api/projects", json={"name": "Dup"})
    r2 = client.post("/api/projects", json={"name": "Dup"})
    assert r2.status_code == 201
    assert r2.json()["id"] == "dup-2"
    r3 = client.post("/api/projects", json={"name": "Dup"})
    assert r3.json()["id"] == "dup-3"


def test_rename_and_delete(client) -> None:
    r = client.post("/api/projects", json={"name": "Alpha"})
    pid = r.json()["id"]
    # Rename doesn't change the slug.
    client.patch(f"/api/projects/{pid}", json={"name": "Beta"})
    row = client.get(f"/api/projects/{pid}").json()
    assert row["id"] == pid
    assert row["project"]["name"] == "Beta"
    # Delete.
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_add_and_remove_video(client, tmp_path) -> None:
    # Seed a video directly in DB so membership has something to point at.
    from server.db import open_connection, run_migrations
    conn = open_connection(tmp_path / "app.db")
    run_migrations(conn)
    conn.execute(
        "INSERT INTO videos(id,url,title,created_at,updated_at) VALUES(?,?,?,?,?)",
        ("abc12345678", "https://x", "Vid", "2026-01-01", "2026-01-01"),
    )
    conn.close()

    pid = client.post("/api/projects", json={"name": "P"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/videos", json={"video_ids": ["abc12345678"]})
    assert r.status_code == 200
    r = client.get(f"/api/projects/{pid}")
    assert r.json()["project"]["video_count"] == 1
    videos = r.json()["videos"]
    assert videos[0]["id"] == "abc12345678"

    # Idempotent: adding again doesn't break.
    r2 = client.post(f"/api/projects/{pid}/videos", json={"video_ids": ["abc12345678"]})
    assert r2.status_code == 200
    assert client.get(f"/api/projects/{pid}").json()["project"]["video_count"] == 1

    # Remove.
    r = client.delete(f"/api/projects/{pid}/videos/abc12345678")
    assert r.status_code == 200
    assert client.get(f"/api/projects/{pid}").json()["project"]["video_count"] == 0


def test_unknown_project_404(client) -> None:
    assert client.get("/api/projects/nope").status_code == 404
    assert client.patch("/api/projects/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/projects/nope").status_code == 404
```

- [ ] **Step 2: Run tests — fails**

Run: `uv run pytest tests/test_projects_api.py -v`
Expected: FAIL (routes missing; `AUTH_DISABLED` unused; `OUTPUT_DIR` not overridden)

- [ ] **Step 3: Implement `server/projects.py`**

```python
# server/projects.py
"""Projects service: create/list/get/rename/delete + video membership.

Slugs are derived from the name, lowercase dashed, max 48 chars. Collisions
auto-suffix (-2, -3, ...). Slug is stable — renaming doesn't change it."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SLUG_PUNCT = re.compile(r"[^a-z0-9]+")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    s = _SLUG_PUNCT.sub("-", (name or "").strip().lower()).strip("-")
    return (s or "project")[:48]


def _ensure_conn(conn, out_dir: Path):
    if conn is not None:
        return conn, False
    from .db import open_connection
    return open_connection(out_dir / "app.db"), True


def _close_if_owned(conn, owned: bool) -> None:
    if owned:
        conn.close()


def create_project(
    out_dir: Path,
    name: str,
    description: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    base = _slugify(name)
    c, owned = _ensure_conn(conn, out_dir)
    try:
        now = _iso_now()
        candidate = base
        suffix = 2
        while True:
            try:
                c.execute(
                    "INSERT INTO projects(id, name, description, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (candidate, name, description, now, now),
                )
                break
            except sqlite3.IntegrityError:
                candidate = f"{base}-{suffix}"
                suffix += 1
                if suffix > 1000:  # paranoid bound
                    raise
        return get_project(out_dir, candidate, conn=c)["project"]
    finally:
        _close_if_owned(c, owned)


def list_projects(
    out_dir: Path, conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        rows = c.execute("""
            SELECT p.*,
                   COUNT(pv.video_id)        AS video_count,
                   COALESCE(SUM(v.duration_sec), 0) AS total_seconds,
                   MAX(pv.added_at)          AS last_activity
              FROM projects p
              LEFT JOIN project_videos pv ON pv.project_id = p.id
              LEFT JOIN videos v           ON v.id = pv.video_id
             GROUP BY p.id
             ORDER BY (CASE WHEN last_activity IS NULL THEN 1 ELSE 0 END),
                      last_activity DESC,
                      p.updated_at DESC
        """).fetchall()
        return [_project_summary(r) for r in rows]
    finally:
        _close_if_owned(c, owned)


def _project_summary(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "video_count": int(row["video_count"] or 0),
        "total_seconds": float(row["total_seconds"] or 0),
        "last_activity": row["last_activity"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_project(
    out_dir: Path,
    project_id: str,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        row = c.execute("""
            SELECT p.*,
                   (SELECT COUNT(*)        FROM project_videos WHERE project_id=p.id) AS video_count,
                   (SELECT COALESCE(SUM(v.duration_sec),0)
                      FROM project_videos pv JOIN videos v ON v.id=pv.video_id
                     WHERE pv.project_id=p.id) AS total_seconds,
                   (SELECT MAX(added_at) FROM project_videos WHERE project_id=p.id) AS last_activity
              FROM projects p WHERE p.id=?
        """, (project_id,)).fetchone()
        if row is None:
            return None
        videos = [
            {
                "id": r["id"],
                "title": r["title"],
                "duration_sec": r["duration_sec"],
                "channel": r["channel"],
                "archived": bool(r["archived"]),
                "added_at": r["added_at"],
            }
            for r in c.execute("""
                SELECT v.id, v.title, v.duration_sec, v.channel, v.archived, pv.added_at
                  FROM project_videos pv JOIN videos v ON v.id=pv.video_id
                 WHERE pv.project_id=? ORDER BY pv.added_at DESC
            """, (project_id,))
        ]
        return {"project": _project_summary(row), "videos": videos}
    finally:
        _close_if_owned(c, owned)


def update_project(
    out_dir: Path,
    project_id: str,
    updates: dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> bool:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        sets: list[str] = []
        vals: list[Any] = []
        if "name" in updates:
            sets.append("name=?")
            vals.append(updates["name"])
        if "description" in updates:
            sets.append("description=?")
            vals.append(updates["description"])
        if not sets:
            return True
        sets.append("updated_at=?")
        vals.append(_iso_now())
        vals.append(project_id)
        cur = c.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE id=?", vals
        )
        return cur.rowcount > 0
    finally:
        _close_if_owned(c, owned)


def delete_project(
    out_dir: Path,
    project_id: str,
    conn: sqlite3.Connection | None = None,
) -> bool:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        cur = c.execute("DELETE FROM projects WHERE id=?", (project_id,))
        return cur.rowcount > 0
    finally:
        _close_if_owned(c, owned)


def add_videos(
    out_dir: Path,
    project_id: str,
    video_ids: list[str],
    conn: sqlite3.Connection | None = None,
) -> int:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        # Verify project exists.
        if c.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            return -1
        now = _iso_now()
        added = 0
        for vid in video_ids:
            # Silently skip unknown videos; caller can decide whether to care.
            if c.execute("SELECT 1 FROM videos WHERE id=?", (vid,)).fetchone() is None:
                continue
            c.execute(
                "INSERT OR IGNORE INTO project_videos(project_id, video_id, added_at) "
                "VALUES(?, ?, ?)",
                (project_id, vid, now),
            )
            added += 1
        return added
    finally:
        _close_if_owned(c, owned)


def remove_video(
    out_dir: Path,
    project_id: str,
    video_id: str,
    conn: sqlite3.Connection | None = None,
) -> bool:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        cur = c.execute(
            "DELETE FROM project_videos WHERE project_id=? AND video_id=?",
            (project_id, video_id),
        )
        return cur.rowcount > 0
    finally:
        _close_if_owned(c, owned)
```

- [ ] **Step 4: Wire routes in `server/main.py`**

Near the existing imports, add:

```python
from . import projects as projects_mod
from .db import open_connection, run_migrations
from .migrate_data import migrate_data
```

In the `auth.py` module, add one line so tests can bypass auth. Edit `server/auth.py` `is_enabled()`:

```python
def is_enabled() -> bool:
    if (os.environ.get("AUTH_DISABLED") or "").strip() == "1":
        return False
    return bool(_user_pairs())
```

At startup in `server/main.py`, right after `state.configure_persistence(...)`, add:

```python
# Open DB, run schema migrations, and do the one-shot data migration if
# the marker hasn't been set. Idempotent — safe on every startup.
try:
    _conn = open_connection(OUTPUT_DIR / "app.db")
    run_migrations(_conn)
    migrate_data(_conn, OUTPUT_DIR)
    _conn.close()
except Exception as e:
    log.warning("DB bootstrap failed: %s", e)
```

Add the project routes (anywhere in the routes block; put them near `/api/transcripts` for locality):

Add at top of `main.py` (with the other imports):

```python
from fastapi import Response
```

Then the new routes (anywhere in the routes block; put them near `/api/transcripts` for locality):

```python
# --- Projects -------------------------------------------------------------


@app.get("/api/projects", dependencies=[Depends(auth.require_http)])
def api_list_projects():
    return projects_mod.list_projects(OUTPUT_DIR)


@app.post("/api/projects", dependencies=[Depends(auth.require_http)])
def api_create_project(body: dict = Body(...), response: Response = None):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    proj = projects_mod.create_project(
        OUTPUT_DIR, name=name, description=body.get("description"),
    )
    if response is not None:
        response.status_code = 201
    return proj


@app.get("/api/projects/{project_id}", dependencies=[Depends(auth.require_http)])
def api_get_project(project_id: str):
    data = projects_mod.get_project(OUTPUT_DIR, project_id)
    if data is None:
        raise HTTPException(status_code=404, detail="not found")
    return data


@app.patch("/api/projects/{project_id}", dependencies=[Depends(auth.require_http)])
def api_update_project(project_id: str, body: dict = Body(...)):
    ok = projects_mod.update_project(OUTPUT_DIR, project_id, body or {})
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return projects_mod.get_project(OUTPUT_DIR, project_id)


@app.delete("/api/projects/{project_id}", dependencies=[Depends(auth.require_http)])
def api_delete_project(project_id: str):
    if not projects_mod.delete_project(OUTPUT_DIR, project_id):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


@app.post("/api/projects/{project_id}/videos", dependencies=[Depends(auth.require_http)])
def api_add_project_videos(project_id: str, body: dict = Body(...)):
    ids = body.get("video_ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="video_ids must be a list")
    added = projects_mod.add_videos(OUTPUT_DIR, project_id, ids)
    if added == -1:
        raise HTTPException(status_code=404, detail="project not found")
    return {"added": added}


@app.delete(
    "/api/projects/{project_id}/videos/{video_id}",
    dependencies=[Depends(auth.require_http)],
)
def api_remove_project_video(project_id: str, video_id: str):
    if not projects_mod.remove_video(OUTPUT_DIR, project_id, video_id):
        raise HTTPException(status_code=404, detail="membership not found")
    return {"ok": True}
```

Make `OUTPUT_DIR` respect the env var (top of `main.py`, replace the existing line):

```python
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR") or (PROJECT_ROOT / "output"))
```

Also, update `api_refresh_metadata` (the existing endpoint) so refreshing YouTube metadata also bumps the DB row. After the block that writes `transcript.json`, add:

```python
    # Keep the DB row in sync so dashboard / library reflect the refreshed metadata.
    try:
        _c = open_connection(OUTPUT_DIR / "app.db")
        try:
            _c.execute(
                "UPDATE videos SET title=?, channel=?, channel_id=?, channel_url=?, "
                "channel_follower_count=?, upload_date=?, view_count=?, like_count=?, "
                "comment_count=?, description=?, updated_at=datetime('now') "
                "WHERE id=?",
                (
                    data.get("title"),
                    data.get("channel"),
                    data.get("channel_id"),
                    data.get("channel_url"),
                    data.get("channel_follower_count"),
                    data.get("upload_date"),
                    data.get("view_count"),
                    data.get("like_count"),
                    data.get("comment_count"),
                    data.get("description"),
                    video_id,
                ),
            )
        finally:
            _c.close()
    except Exception as e:
        log.warning("refresh-metadata DB update failed for %s: %s", video_id, e)
```

- [ ] **Step 5: Run tests — pass**

Run: `uv run pytest tests/test_projects_api.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Run full suite to catch regressions**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add server/projects.py server/main.py server/auth.py tests/test_projects_api.py
git commit -m "projects: CRUD + membership service and /api/projects routes"
```

---

### Task 8: /api/stats for dashboard

**Files:**
- Modify: `server/main.py` — add `/api/stats`
- Create: `tests/test_stats_api.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_stats_api.py
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("AUTH_DISABLED", "1")
    import importlib
    import server.main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_empty_stats(client) -> None:
    r = client.get("/api/stats")
    assert r.status_code == 200
    d = r.json()
    assert d == {
        "video_count": 0,
        "project_count": 0,
        "total_seconds": 0.0,
        "storage_bytes": 0,
        "latest_videos": [],
    }


def test_populated_stats(client, tmp_path) -> None:
    from server.db import open_connection, run_migrations
    c = open_connection(tmp_path / "app.db")
    run_migrations(c)
    c.execute(
        "INSERT INTO videos(id,url,title,duration_sec,storage_bytes,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("abc12345678", "https://x", "V1", 120.0, 500, "2026-01-01", "2026-01-01"),
    )
    c.execute(
        "INSERT INTO videos(id,url,title,duration_sec,storage_bytes,archived,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("zzz12345678", "https://y", "V2 archived", 60.0, 100, 1, "2026-01-02", "2026-01-02"),
    )
    c.execute(
        "INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("p1", "P1", "2026-01-01", "2026-01-01"),
    )
    c.close()

    d = client.get("/api/stats").json()
    # Archived videos are excluded from totals.
    assert d["video_count"] == 1
    assert d["project_count"] == 1
    assert d["total_seconds"] == pytest.approx(120.0)
    assert d["storage_bytes"] == 500
    assert len(d["latest_videos"]) == 1
    assert d["latest_videos"][0]["id"] == "abc12345678"
```

- [ ] **Step 2: Run test — fails**

Run: `uv run pytest tests/test_stats_api.py -v`
Expected: FAIL (404)

- [ ] **Step 3: Implement `/api/stats`**

Add to `server/main.py`:

```python
@app.get("/api/stats", dependencies=[Depends(auth.require_http)])
def api_stats():
    conn = open_connection(OUTPUT_DIR / "app.db")
    try:
        run_migrations(conn)
        video_count = conn.execute(
            "SELECT COUNT(*) FROM videos WHERE archived = 0"
        ).fetchone()[0]
        totals = conn.execute(
            "SELECT COALESCE(SUM(duration_sec),0), COALESCE(SUM(storage_bytes),0) "
            "FROM videos WHERE archived = 0"
        ).fetchone()
        project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        latest = [
            {
                "id": r["id"],
                "title": r["title"],
                "duration_sec": r["duration_sec"],
                "channel": r["channel"],
                "created_at": r["created_at"],
            }
            for r in conn.execute(
                "SELECT id, title, duration_sec, channel, created_at "
                "FROM videos WHERE archived = 0 ORDER BY created_at DESC LIMIT 8"
            )
        ]
        return {
            "video_count": int(video_count),
            "project_count": int(project_count),
            "total_seconds": float(totals[0] or 0),
            "storage_bytes": int(totals[1] or 0),
            "latest_videos": latest,
        }
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests — pass**

Run: `uv run pytest tests/test_stats_api.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Full suite**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add server/main.py tests/test_stats_api.py
git commit -m "stats: /api/stats endpoint for dashboard rollups"
```

---

### Task 9: Frontend — Dashboard + routing + Sidebar

**Files:**
- Create: `web/src/projects.ts` — fetch helpers
- Create: `web/src/Dashboard.tsx`
- Create: `web/src/components/ProjectCard.tsx`
- Create: `web/src/components/StatsStrip.tsx`
- Create: `web/src/components/NewProjectModal.tsx`
- Modify: `web/src/App.tsx` — routes: `/` → Dashboard, `/library` → Library, `/p/:slug` → Project
- Modify: `web/src/components/Sidebar.tsx` — add Dashboard + Projects nav
- Modify: `web/src/types.ts` — add `Project`, `ProjectDetail`, `Stats`

- [ ] **Step 1: Types**

Append to `web/src/types.ts`:

```typescript
export interface Project {
  id: string;
  name: string;
  description: string | null;
  video_count: number;
  total_seconds: number;
  last_activity: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectVideoEntry {
  id: string;
  title: string | null;
  duration_sec: number | null;
  channel: string | null;
  archived: boolean;
  added_at: string;
}

export interface ProjectDetail {
  project: Project;
  videos: ProjectVideoEntry[];
}

export interface StatsVideo {
  id: string;
  title: string | null;
  duration_sec: number | null;
  channel: string | null;
  created_at: string;
}

export interface Stats {
  video_count: number;
  project_count: number;
  total_seconds: number;
  storage_bytes: number;
  latest_videos: StatsVideo[];
}
```

- [ ] **Step 2: API client**

Create `web/src/projects.ts`:

```typescript
import { authFetch } from "./auth";
import type { Project, ProjectDetail, Stats } from "./types";

export async function listProjects(): Promise<Project[]> {
  const r = await authFetch("/api/projects");
  if (!r.ok) throw new Error(`listProjects: ${r.status}`);
  return r.json();
}

export async function createProject(name: string, description?: string): Promise<Project> {
  const r = await authFetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description: description ?? null }),
  });
  if (!r.ok) throw new Error(`createProject: ${r.status}`);
  return r.json();
}

export async function getProject(id: string): Promise<ProjectDetail> {
  const r = await authFetch(`/api/projects/${id}`);
  if (!r.ok) throw new Error(`getProject: ${r.status}`);
  return r.json();
}

export async function updateProject(id: string, patch: Partial<{ name: string; description: string | null }>): Promise<ProjectDetail> {
  const r = await authFetch(`/api/projects/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`updateProject: ${r.status}`);
  return r.json();
}

export async function deleteProject(id: string): Promise<void> {
  const r = await authFetch(`/api/projects/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`deleteProject: ${r.status}`);
}

export async function addVideosToProject(id: string, videoIds: string[]): Promise<{ added: number }> {
  const r = await authFetch(`/api/projects/${id}/videos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_ids: videoIds }),
  });
  if (!r.ok) throw new Error(`addVideosToProject: ${r.status}`);
  return r.json();
}

export async function removeVideoFromProject(projectId: string, videoId: string): Promise<void> {
  const r = await authFetch(`/api/projects/${projectId}/videos/${videoId}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`removeVideoFromProject: ${r.status}`);
}

export async function getStats(): Promise<Stats> {
  const r = await authFetch("/api/stats");
  if (!r.ok) throw new Error(`getStats: ${r.status}`);
  return r.json();
}
```

- [ ] **Step 3: `StatsStrip` component**

Create `web/src/components/StatsStrip.tsx`:

```tsx
import type { Stats } from "../types";

function fmtHours(sec: number): string {
  const h = sec / 3600;
  if (h >= 10) return `${Math.round(h)}h`;
  if (h >= 1) return `${h.toFixed(1)}h`;
  const m = Math.round(sec / 60);
  return `${m}m`;
}

function fmtBytes(n: number): string {
  if (n <= 0) return "0";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v < 10 ? 1 : 0)}${units[i]}`;
}

export default function StatsStrip({ stats }: { stats: Stats }) {
  return (
    <div className="stats-strip">
      <div className="stat"><span className="stat-num">{stats.video_count}</span><span className="stat-lbl">videos</span></div>
      <div className="stat"><span className="stat-num">{fmtHours(stats.total_seconds)}</span><span className="stat-lbl">content</span></div>
      <div className="stat"><span className="stat-num">{stats.project_count}</span><span className="stat-lbl">projects</span></div>
      <div className="stat"><span className="stat-num">{fmtBytes(stats.storage_bytes)}</span><span className="stat-lbl">storage</span></div>
    </div>
  );
}
```

- [ ] **Step 4: `ProjectCard` component**

Create `web/src/components/ProjectCard.tsx`:

```tsx
import { Link } from "react-router-dom";
import type { Project } from "../types";

export default function ProjectCard({ p }: { p: Project }) {
  const hours = p.total_seconds >= 3600
    ? `${(p.total_seconds / 3600).toFixed(1)}h`
    : `${Math.round(p.total_seconds / 60)}m`;
  return (
    <Link to={`/p/${p.id}`} className="project-card">
      <div className="project-card-name">{p.name}</div>
      {p.description && <div className="project-card-desc">{p.description}</div>}
      <div className="project-card-meta">
        <span>{p.video_count} videos</span>
        <span>·</span>
        <span>{hours}</span>
      </div>
    </Link>
  );
}
```

- [ ] **Step 5: `NewProjectModal` component**

Create `web/src/components/NewProjectModal.tsx`:

```tsx
import { useState, type FormEvent } from "react";

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmit: (name: string, description: string) => Promise<void> | void;
}

export default function NewProjectModal({ open, onClose, onSubmit }: Props) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(name.trim(), desc.trim());
      setName("");
      setDesc("");
      onClose();
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>New project</h2>
        <form onSubmit={submit}>
          <label>
            Name
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            Description <span className="hint">(optional)</span>
            <textarea rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} />
          </label>
          {error && <div className="form-error">{error}</div>}
          <div className="modal-actions">
            <button type="button" onClick={onClose}>Cancel</button>
            <button type="submit" disabled={submitting || !name.trim()}>Create</button>
          </div>
        </form>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Dashboard page**

Create `web/src/Dashboard.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import TopBar from "./components/TopBar";
import ProjectCard from "./components/ProjectCard";
import StatsStrip from "./components/StatsStrip";
import NewProjectModal from "./components/NewProjectModal";
import VideoRow from "./components/VideoRow";
import { createProject, getStats, listProjects } from "./projects";
import type { Project, Stats } from "./types";

interface Props {
  onMenuToggle?: () => void;
  onAdd: () => void;
}

export default function Dashboard({ onMenuToggle, onAdd }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const reload = useCallback(() => {
    listProjects().then(setProjects).catch(console.error);
    getStats().then(setStats).catch(console.error);
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const handleCreate = useCallback(async (name: string, description: string) => {
    await createProject(name, description || undefined);
    reload();
  }, [reload]);

  return (
    <div className="dashboard">
      <TopBar
        onMenuToggle={onMenuToggle}
        onAdd={onAdd}
        title="Dashboard"
      />
      <div className="dash-body">
        <section className="dash-hero">
          <h1>Your projects</h1>
          <div className="dash-hero-actions">
            <button className="primary" onClick={() => setModalOpen(true)}>New project</button>
            <button onClick={onAdd}>Quick ingest</button>
          </div>
        </section>
        <section className="dash-projects">
          {projects.length === 0 ? (
            <div className="empty">No projects yet. Create one to start grouping videos.</div>
          ) : (
            <div className="project-grid">
              {projects.map((p) => <ProjectCard key={p.id} p={p} />)}
            </div>
          )}
        </section>
        <section className="dash-latest">
          <h2>Latest videos</h2>
          {stats && stats.latest_videos.length > 0 ? (
            <div className="latest-list">
              {stats.latest_videos.map((v) => (
                <VideoRow
                  key={v.id}
                  item={{
                    id: v.id,
                    title: v.title ?? v.id,
                    duration_sec: v.duration_sec,
                    language: null,
                    diarized: false,
                    model: null,
                    segment_count: 0,
                    channel: v.channel,
                  }}
                  onArchive={() => {}}
                />
              ))}
            </div>
          ) : (
            <div className="empty">No videos yet. Paste a URL to get started.</div>
          )}
        </section>
        {stats && <StatsStrip stats={stats} />}
      </div>
      <NewProjectModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSubmit={handleCreate}
      />
    </div>
  );
}
```

- [ ] **Step 7: Wire routes in `App.tsx`**

Edit `web/src/App.tsx`:

Replace the `Routes` block:

```tsx
import Dashboard from "./Dashboard";
import Project from "./Project";

// ... inside Shell() ...

      <Routes>
        <Route
          path="/"
          element={
            <Dashboard
              onMenuToggle={toggleDrawer}
              onAdd={() => setIngestOpen(true)}
            />
          }
        />
        <Route
          path="/library"
          element={
            <Library
              onAdd={() => setIngestOpen(true)}
              onMenuToggle={toggleDrawer}
              refreshKey={refreshKey}
            />
          }
        />
        <Route
          path="/p/:projectId"
          element={<Project onMenuToggle={toggleDrawer} />}
        />
        <Route
          path="/v/:videoId"
          element={
            <Detail
              pendingIngestUrl={pendingIngestUrl}
              pendingIngestOpts={pendingOpts}
              onPendingIngestConsumed={() => { setPendingIngestUrl(null); setPendingOpts(null); }}
              onIngestDone={refresh}
              onMenuToggle={toggleDrawer}
            />
          }
        />
        <Route path="/archive" element={<Archive onMenuToggle={toggleDrawer} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
```

- [ ] **Step 8: Sidebar links**

Before editing, read `web/src/components/Sidebar.tsx` end-to-end so the changes match the existing Link / NavLink pattern and styling classes. Then:

1. Change the existing "Library" link target from `/` → `/library` (its label stays "Library").
2. Add a new top item **Dashboard** → `/`, placed above "Library".
3. Add a new section **Projects**:
   - On mount, `listProjects()` and store in state.
   - Render up to 8 projects as `<Link to={`/p/${p.id}`}>{p.name}</Link>`.
   - If `>8`, render a "Show all" link to `/projects` (the flat list page is out of scope this slice — leave the link commented out with a TODO pointing at Slice 2).
   - A "+ New project" button opens `NewProjectModal` (same one used by Dashboard). On success, re-fetch project list and navigate to `/p/<id>`.
4. Do NOT touch existing Archive / New Ingest links.

Use exactly the same auth-fetch pattern the file already uses; do not introduce a second data-fetching convention.

- [ ] **Step 9: Build + type-check**

```bash
cd web && npm run build
```

Expected: build succeeds. Any type errors → fix inline before continuing.

- [ ] **Step 10: Commit**

```bash
git add web/src/Dashboard.tsx web/src/projects.ts \
        web/src/components/ProjectCard.tsx web/src/components/StatsStrip.tsx \
        web/src/components/NewProjectModal.tsx \
        web/src/App.tsx web/src/components/Sidebar.tsx web/src/types.ts
git commit -m "web: Dashboard page + projects API client + sidebar projects"
```

---

### Task 10: Frontend — Project page (read-only; Add Videos comes in Slice 2)

**Files:**
- Create: `web/src/Project.tsx`

- [ ] **Step 1: Write the page**

Create `web/src/Project.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import TopBar from "./components/TopBar";
import VideoRow from "./components/VideoRow";
import ConfirmDialog from "./components/ConfirmDialog";
import { deleteProject, getProject, updateProject, removeVideoFromProject } from "./projects";
import type { ProjectDetail } from "./types";

interface Props { onMenuToggle?: () => void; }

export default function Project({ onMenuToggle }: Props) {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<ProjectDetail | null>(null);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const reload = useCallback(() => {
    if (!projectId) return;
    getProject(projectId).then(setData).catch(() => setData(null));
  }, [projectId]);

  useEffect(() => { reload(); }, [reload]);

  const saveName = useCallback(async () => {
    if (!projectId) return;
    if (nameDraft.trim() && data && nameDraft.trim() !== data.project.name) {
      await updateProject(projectId, { name: nameDraft.trim() });
    }
    setEditingName(false);
    reload();
  }, [projectId, nameDraft, data, reload]);

  const handleRemove = useCallback(async (vid: string) => {
    await removeVideoFromProject(projectId, vid);
    reload();
  }, [projectId, reload]);

  const handleDelete = useCallback(async () => {
    await deleteProject(projectId);
    navigate("/");
  }, [projectId, navigate]);

  if (!projectId) return null;
  if (data === null) {
    return (
      <div className="project-page">
        <TopBar onMenuToggle={onMenuToggle} title="Project" />
        <div className="empty">Project not found. <Link to="/">Back to dashboard</Link>.</div>
      </div>
    );
  }

  const { project, videos } = data;
  return (
    <div className="project-page">
      <TopBar onMenuToggle={onMenuToggle} title={project.name} />
      <div className="project-body">
        <header className="project-header">
          {editingName ? (
            <input
              autoFocus
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
              onBlur={saveName}
              onKeyDown={(e) => { if (e.key === "Enter") saveName(); }}
            />
          ) : (
            <h1 onClick={() => { setEditingName(true); setNameDraft(project.name); }}>
              {project.name}
            </h1>
          )}
          {project.description && <p className="project-desc">{project.description}</p>}
          <div className="project-meta">
            <span>{project.video_count} videos</span>
          </div>
          <div className="project-actions">
            <button disabled title="Coming in the next slice">Add videos</button>
            <button className="danger" onClick={() => setConfirmDelete(true)}>Delete project</button>
          </div>
        </header>
        <section>
          {videos.length === 0 ? (
            <div className="empty">
              No videos yet. Adding videos lands in the next slice.
            </div>
          ) : (
            videos.map((v) => (
              <div key={v.id} className="project-video-row">
                <VideoRow
                  item={{
                    id: v.id,
                    title: v.title ?? v.id,
                    duration_sec: v.duration_sec,
                    language: null,
                    diarized: false,
                    model: null,
                    segment_count: 0,
                    channel: v.channel,
                  }}
                  onArchive={() => {}}
                />
                <button
                  className="row-remove"
                  title="Remove from project (video itself is not deleted)"
                  onClick={() => handleRemove(v.id)}
                >
                  Remove
                </button>
              </div>
            ))
          )}
        </section>
      </div>
      <ConfirmDialog
        open={confirmDelete}
        title="Delete project?"
        message={`"${project.name}" will be removed. Videos are not affected.`}
        confirmLabel="Delete"
        onCancel={() => setConfirmDelete(false)}
        onConfirm={handleDelete}
      />
    </div>
  );
}
```

- [ ] **Step 2: Build + smoke**

Run: `cd web && npm run build`
Expected: clean. TypeScript errors get fixed inline.

- [ ] **Step 3: Commit**

```bash
git add web/src/Project.tsx
git commit -m "web: Project detail page (read-only; Add Videos lands next slice)"
```

---

### Task 11: Slice 1 verification — full-stack smoke

- [ ] **Step 1: Back-end tests**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS (7 test files, ~25 tests).

- [ ] **Step 2: Type-check frontend**

Run: `cd web && npx tsc -b --noEmit ; cd ..`
Expected: exit 0.

- [ ] **Step 3: Manual backup — critical before touching the real DB**

```bash
# Safety: copy the real output/ tree. Migration is additive but we want
# a rollback path before a user's actual data is touched.
cp -r output output.pre-slice1-backup
```

- [ ] **Step 4: Restart the server**

Kill the existing FastAPI bg task (user's handoff: `bzw5whe2a`), then:

```powershell
& scripts/run-server.ps1
```

Watch the log: you should see "loaded N ingest records" (existing) AND new lines from the migration showing video / tags / analysis counts.

- [ ] **Step 5: Open the app**

Open `http://localhost:8000/` → Dashboard should render.
Verify:
- `/api/stats` shows correct video count (should match what was in `output/`).
- Create a project — it shows up as a card and in the sidebar.
- Click into the project → empty state, "Add videos" disabled with tooltip.
- `/library` shows the old flat list of videos.
- `/v/<some-id>` still works (detail page unchanged).
- Rename a video's tags via the Detail view (PATCH /meta) — reload → tags persist.

- [ ] **Step 6: Rebuild the web/dist for tunnel serving**

```bash
cd web && npm run build
```

- [ ] **Step 7: Final commit**

If any fixes were needed during smoke, commit them:

```bash
git commit -am "slice1: smoke-test fixes"
```

Then tag Slice 1:

```bash
git tag slice-1-foundation
```
