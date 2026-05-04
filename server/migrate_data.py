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


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _folder_bytes(folder: Path) -> int:
    """Sum sizes of every real file under `folder`. Skips symlinks so we
    don't double-count after migration creates analyses/<id>.json symlinks
    back into the folder."""
    total = 0
    for root, _, files in os.walk(folder, followlinks=False):
        for name in files:
            p = Path(root) / name
            try:
                st = p.lstat()
            except OSError:
                continue
            # S_IFLNK check via lstat — skip symlinks entirely.
            import stat as _stat
            if _stat.S_ISLNK(st.st_mode):
                continue
            total += st.st_size
    return total


def _as_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _upsert_video(
    conn, out_dir: Path, video_id: str, tj: dict, meta: dict,
    archived: bool, *, folder: Path | None = None,
) -> bool:
    """Insert videos row if missing. Returns True if a new row was inserted.

    `folder` lets the caller pass the actual on-disk folder under the new
    folder-per-project layout. Falls back to the legacy flat path for
    backwards compat in tests / fresh seeders.

    Derives project_id from the folder location: `output/projects/<X>/<vid>/`
    gives project_id=X. Anything else falls into Inbox."""
    existing = conn.execute(
        "SELECT id FROM videos WHERE id = ?", (video_id,)
    ).fetchone()
    if existing is not None:
        return False

    if folder is None:
        folder = out_dir / video_id
    # Derive project_id from the folder path.
    project_id = "inbox"
    try:
        rel = folder.resolve().relative_to((out_dir / "projects").resolve())
        # rel like "<project_id>/<video_id>"; first part is the project.
        parts = rel.parts
        if parts:
            project_id = parts[0]
    except (ValueError, OSError):
        pass
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
            created_at, updated_at,
            project_id, path
        ) VALUES (
            :id, :url, :title, :channel, :channel_id, :channel_url,
            :channel_follower_count, :upload_date, :duration_sec, :description,
            :categories, :yt_tags, :view_count, :like_count, :comment_count,
            :language, :language_probability, :diarized, :speaker_count,
            :segment_count, :model, :compute_type, :batched, :batch_size,
            :transcription_elapsed_sec, :transcription_realtime_factor,
            :storage_bytes, :archived, :notes, :owner, :transcribed_at,
            :created_at, :updated_at,
            :project_id, :path
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
            "project_id": project_id,
            "path": str(folder),
        },
    )
    return True


def _insert_tags(conn, video_id: str, tags: list) -> None:
    if not tags:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO video_tags(video_id, tag) VALUES(?, ?)",
        [(video_id, t) for t in tags if t],
    )


def _insert_speakers(conn, video_id: str, speakers: dict) -> None:
    if not speakers:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO video_speakers(video_id, label, name) VALUES(?, ?, ?)",
        [(video_id, label, name) for label, name in speakers.items() if name],
    )


def _migrate_analysis(conn, folder: Path, video_id: str) -> bool:
    """If analysis.json exists and no analyses row recorded yet, insert
    one row and move the file into analyses/<id>.json with a symlink (or
    plain copy fallback). Returns True on first-time migration."""
    src = folder / "analysis.json"
    if not src.exists():
        return False
    existing = conn.execute(
        "SELECT id FROM analyses WHERE video_id = ?", (video_id,)
    ).fetchone()
    if existing is not None:
        # A stale analysis.json after migration most likely means a manual
        # re-run dropped a new file; surface it rather than silently ignore.
        log.warning(
            "analysis.json present for %s but analyses row already recorded; "
            "leaving the file untouched",
            video_id,
        )
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
    # Copy before touching src — target becomes the archival truth.
    shutil.copy2(src, target)

    # Try to convert src into a symlink pointing at target, atomically.
    # If any step fails, src stays intact as a plain file — target is still
    # the authoritative archival copy. Never leaves src in a missing state.
    tmp = src.with_name(src.name + ".link.tmp")
    try:
        if tmp.exists():
            tmp.unlink()
        os.symlink(target.name, tmp, target_is_directory=False)
        os.replace(tmp, src)  # atomic on both POSIX and Windows
    except (OSError, NotImplementedError) as e:
        log.warning(
            "symlink unavailable (%s); analysis.json kept as plain file", e,
        )
        # Cleanup any partial tmp; swallow errors (best-effort).
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

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


def migrate_data(conn, output_dir: Path) -> dict:
    """Walk output/ and populate SQLite. Idempotent. Returns counts.

    Handles both the legacy flat layout (output/<id>/) and the
    folder-per-project layout (output/projects/<project>/<id>/) introduced
    in migration 004. The seeder skips rows that already exist in the DB,
    so on an already-migrated install this is a no-op walk.
    """
    run_migrations(conn)
    # Ensure the Inbox project exists so seeded rows have somewhere to
    # land project-id-wise.
    from .projects import ensure_inbox
    ensure_inbox(output_dir, conn=conn)

    videos_migrated = 0
    analyses_migrated = 0

    if not output_dir.exists():
        return {
            "videos_migrated": 0,
            "analyses_migrated": 0,
            "ingests_migrated": 0,
        }

    # Walk both layouts. Set semantics ensure no double-seed if a stray
    # flat folder shadows a properly-nested one.
    from .layout import iter_video_dirs
    candidate_dirs: list[Path] = list(iter_video_dirs(output_dir))
    seen_names = {p.name for p in candidate_dirs}
    for entry in sorted(output_dir.iterdir()):
        if not entry.is_dir() or entry.name in seen_names:
            continue
        if entry.name in {"projects", "_ingests"}:
            continue
        candidate_dirs.append(entry)

    conn.execute("BEGIN")
    try:
        for sub in sorted(candidate_dirs, key=lambda p: p.name):
            tj_path = sub / "transcript.json"
            if not tj_path.exists():
                continue
            try:
                tj = json.loads(tj_path.read_text(encoding="utf-8"))
            except Exception:
                log.warning("skipping %s: bad transcript.json", sub.name)
                continue
            # Defensive: real YouTube IDs can begin with `_`, so we can't
            # filter by name prefix. Instead, require the JSON root to be a
            # dict — catches bogus leftovers like the old output/_ingests/
            # folder that stored a segments-only list.
            if not isinstance(tj, dict):
                log.warning(
                    "skipping %s: transcript.json is not an object (got %s)",
                    sub.name, type(tj).__name__,
                )
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
                conn, output_dir, video_id, tj, meta, archived, folder=sub,
            )
            if inserted:
                videos_migrated += 1
                _insert_tags(conn, video_id, meta.get("tags") or [])
                _insert_speakers(conn, video_id, meta.get("speaker_names") or {})
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
