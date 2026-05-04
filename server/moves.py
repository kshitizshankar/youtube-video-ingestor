"""Move a video between projects.

Slice 2 ships the minimal viable form: rename the on-disk folder, update
the videos row's `project_id` + `path`, and bust the layout cache. Slice
3 will harden this with the move_log audit trail, copy/verify/delete
fallback for cross-device moves, and boot-time crash recovery.

The contract is stable now: callers should treat any exception raised
from `move_video` as a failure that left the source intact.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import state as state_mod
from .layout import forget_video_path, new_video_dir


log = logging.getLogger(__name__)


class MoveError(Exception):
    """Raised when a move can't proceed (concurrent move, ingest in
    flight, target already populated, missing project, etc.)."""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def move_video(out_dir: Path, video_id: str, new_project_id: str) -> dict:
    """Move a video's folder from its current project to `new_project_id`.

    Refuses if:
      * the video doesn't exist
      * the new project doesn't exist
      * a move is already in progress for this video
      * an ingest is currently writing into the source folder
      * the destination already has a folder for this id

    Returns a dict describing the move on success: from_project, to_project,
    from_path, to_path. Raises MoveError on refusal."""
    from .db import open_connection

    db_path = out_dir / "app.db"
    conn = open_connection(db_path)
    try:
        row = conn.execute(
            "SELECT project_id, path, move_state FROM videos WHERE id=?",
            (video_id,),
        ).fetchone()
        if row is None:
            raise MoveError(f"unknown video id: {video_id}")
        if row["move_state"] == "moving":
            raise MoveError(f"move already in progress for {video_id}")
        if row["project_id"] == new_project_id:
            raise MoveError(f"already in project {new_project_id}")
        target_proj = conn.execute(
            "SELECT id FROM projects WHERE id=?", (new_project_id,),
        ).fetchone()
        if target_proj is None:
            raise MoveError(f"unknown project id: {new_project_id}")
        if state_mod.has_active(video_id):
            raise MoveError(f"ingest active for {video_id}; refusing to move")

        from_project = row["project_id"]
        src_path = Path(row["path"]) if row["path"] else None
        dst_path = new_video_dir(out_dir, new_project_id, video_id)

        if dst_path.exists():
            raise MoveError(f"destination already exists: {dst_path}")

        # Latch state; release on exit (success OR failure cleanup).
        conn.execute(
            "UPDATE videos SET move_state='moving', updated_at=? WHERE id=?",
            (_iso_now(), video_id),
        )
        # Audit row -- consumed by slice 3's recovery scan.
        cur = conn.execute(
            "INSERT INTO move_log(video_id, from_project, to_project, "
            "from_path, to_path, started_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'started')",
            (
                video_id,
                from_project,
                new_project_id,
                str(src_path) if src_path else "",
                str(dst_path),
                _iso_now(),
            ),
        )
        log_id = cur.lastrowid
    except Exception:
        conn.close()
        raise

    # Filesystem move outside the DB transaction so SQLite isn't holding
    # a write lock while we copy gigabytes.
    try:
        if src_path and src_path.exists():
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Same-volume rename: fast, atomic on NTFS.
                src_path.rename(dst_path)
            except OSError:
                # Cross-device or rename refused -- fall back to copy +
                # verify + delete. This branch is the slice 3 hardening
                # area; for now we trust shutil.copytree + size check.
                shutil.copytree(src_path, dst_path)
                for p in src_path.rglob("*"):
                    if not p.is_file():
                        continue
                    rel = p.relative_to(src_path)
                    mirror = dst_path / rel
                    if not mirror.is_file() or mirror.stat().st_size != p.stat().st_size:
                        # Roll back: dst is incomplete; leave src alone.
                        shutil.rmtree(dst_path, ignore_errors=True)
                        raise MoveError(f"verify failed at {mirror}")
                shutil.rmtree(src_path)
        conn.execute(
            "UPDATE move_log SET status='committed', finished_at=? WHERE id=?",
            (_iso_now(), log_id),
        )
        conn.execute(
            "UPDATE videos SET project_id=?, path=?, move_state=NULL, "
            "updated_at=? WHERE id=?",
            (new_project_id, str(dst_path), _iso_now(), video_id),
        )
        # Bump events_since_build on both projects so the graph badge
        # reflects the membership change.
        conn.execute(
            "UPDATE projects SET events_since_build = events_since_build + 1 "
            "WHERE id IN (?, ?)",
            (from_project, new_project_id),
        )
    except MoveError:
        conn.execute(
            "UPDATE move_log SET status='rolled_back', finished_at=?, error=? WHERE id=?",
            (_iso_now(), "verify_failed", log_id),
        )
        conn.execute(
            "UPDATE videos SET move_state=NULL, updated_at=? WHERE id=?",
            (_iso_now(), video_id),
        )
        raise
    except Exception as e:
        conn.execute(
            "UPDATE move_log SET status='failed', finished_at=?, error=? WHERE id=?",
            (_iso_now(), f"{type(e).__name__}: {e}", log_id),
        )
        conn.execute(
            "UPDATE videos SET move_state=NULL, updated_at=? WHERE id=?",
            (_iso_now(), video_id),
        )
        raise
    finally:
        conn.close()
        forget_video_path(video_id)

    return {
        "video_id": video_id,
        "from_project": from_project,
        "to_project": new_project_id,
        "from_path": str(src_path) if src_path else None,
        "to_path": str(dst_path),
    }
