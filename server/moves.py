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
        # has_active() is the only synchronisation we have against an
        # ingest writing into the source folder. The check is not
        # strictly atomic with the latch write below -- a job that
        # transitions from "queued" to "downloading" between the check
        # and the rename would slip through. The vidan server runs as a
        # single uvicorn worker, so this race window is constrained to
        # one process; make it fully airtight requires a worker-side
        # path-stable read on each ingest phase boundary, which the spec
        # acknowledges as future work.
        if state_mod.has_active(video_id):
            raise MoveError(f"ingest active for {video_id}; refusing to move")

        from_project = row["project_id"]
        # Pre-migration rows have NULL path; refuse the move rather than
        # silently committing a destination that we never populated.
        if not row["path"]:
            raise MoveError(
                "video has no path on disk; run the folder migration first"
            )
        src_path = Path(row["path"])
        dst_path = new_video_dir(out_dir, new_project_id, video_id)

        if dst_path.exists():
            raise MoveError(f"destination already exists: {dst_path}")

        # TX1: latch + audit row in a single atomic transaction so a
        # crash between them can't leave a stale move_state with no log
        # row to drive recovery.
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE videos SET move_state='moving', updated_at=? WHERE id=?",
                (_iso_now(), video_id),
            )
            cur = conn.execute(
                "INSERT INTO move_log(video_id, from_project, to_project, "
                "from_path, to_path, started_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'started')",
                (
                    video_id,
                    from_project,
                    new_project_id,
                    str(src_path),
                    str(dst_path),
                    _iso_now(),
                ),
            )
            log_id = cur.lastrowid
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
    except Exception:
        conn.close()
        raise

    # Filesystem move outside the DB transaction so SQLite isn't holding
    # a write lock while we copy gigabytes. Status transitions:
    #   started -> verified -> committed
    # `verified` means the destination is fully populated and the source
    # is gone (or has been verified intact). Boot-time recovery uses
    # 'verified' to detect a crash between the FS work and the DB swap
    # (case C/D in the spec).
    try:
        if src_path.exists():
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Same-volume rename: fast, atomic on NTFS.
                src_path.rename(dst_path)
            except OSError:
                # Cross-device or rename refused -- fall back to copy +
                # verify + delete.
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
        # TX2: status transitions + DB swap + counter bump as ONE atomic
        # commit. The 'verified' write is the marker recovery uses to
        # tell apart "FS done, commit not done" (case C/D) from "started
        # but never finished" (case A/B), so we write it inside the
        # transaction and step it forward to 'committed' before COMMIT.
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE move_log SET status='verified' WHERE id=?",
                (log_id,),
            )
            conn.execute(
                "UPDATE move_log SET status='committed', finished_at=? WHERE id=?",
                (_iso_now(), log_id),
            )
            conn.execute(
                "UPDATE videos SET project_id=?, path=?, move_state=NULL, "
                "updated_at=? WHERE id=?",
                (new_project_id, str(dst_path), _iso_now(), video_id),
            )
            # Bump events_since_build on both projects so the graph
            # badge reflects the membership change. Bumping inside TX2
            # means a crash before COMMIT preserves both the move and
            # the bump as a single rolled-back unit.
            conn.execute(
                "UPDATE projects SET events_since_build = events_since_build + 1 "
                "WHERE id IN (?, ?)",
                (from_project, new_project_id),
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
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


# ---------------------------------------------------------------------------
# Crash recovery (called from main.py at startup, before serving requests).
# ---------------------------------------------------------------------------


def recover_in_flight_moves(out_dir: Path) -> dict:
    """Scan for moves that were in flight when the server crashed and
    bring them to a clean terminal state. Cases (matching the spec):

      A: status='started', src exists, dst doesn't
         -> copy never started; clear move_state, mark rolled_back.
      B: status='started', both exist
         -> copy may be partial; rmtree(dst); same as A.
      C: status='verified', both exist
         -> verify done but commit didn't; rerun the DB swap, clean src.
      D: status='verified', only dst exists
         -> move done in FS, DB never updated; rerun the DB swap.
      E: status='started', neither exists
         -> the source vanished; we can't recover automatically. Log a
            warning and leave the row's move_state cleared so a manual
            operator can reassign.

    Returns a dict of counts per resolution kind for logging."""
    from .db import open_connection

    db_path = out_dir / "app.db"
    if not db_path.exists():
        return {"recovered": 0, "rolled_back": 0, "manual_review": 0}

    conn = open_connection(db_path)
    counts = {"recovered": 0, "rolled_back": 0, "manual_review": 0}
    try:
        # Find every video with move_state != NULL OR an unfinished
        # move_log row. Build a unique work list keyed on video_id.
        rows = conn.execute(
            "SELECT v.id AS video_id, v.move_state, v.project_id AS current_project, "
            "       v.path AS current_path "
            "FROM videos v WHERE v.move_state IS NOT NULL"
        ).fetchall()
        for r in rows:
            video_id = r["video_id"]
            log_row = conn.execute(
                "SELECT id, from_project, to_project, from_path, to_path, status "
                "FROM move_log WHERE video_id=? AND status IN ('started','verified','committed') "
                "ORDER BY id DESC LIMIT 1",
                (video_id,),
            ).fetchone()
            if log_row is None:
                # move_state set but no log row -- inconsistent. Clear
                # the latch so the operator can take over.
                conn.execute(
                    "UPDATE videos SET move_state=NULL, updated_at=? WHERE id=?",
                    (_iso_now(), video_id),
                )
                counts["manual_review"] += 1
                continue
            # status='committed' but move_state still set means TX2
            # crashed mid-flight. The DB swap may or may not have
            # happened; trust whichever path actually has the folder
            # and clean up. This case is symmetrical to verified.
            if log_row["status"] == "committed":
                dst = Path(log_row["to_path"]) if log_row["to_path"] else None
                if dst and dst.is_dir():
                    conn.execute(
                        "UPDATE videos SET move_state=NULL, project_id=?, path=?, "
                        "updated_at=? WHERE id=?",
                        (
                            log_row["to_project"], str(dst),
                            _iso_now(), video_id,
                        ),
                    )
                    conn.execute(
                        "UPDATE projects SET events_since_build = events_since_build + 1 "
                        "WHERE id IN (?, ?)",
                        (log_row["from_project"], log_row["to_project"]),
                    )
                    counts["recovered"] += 1
                    forget_video_path(video_id)
                else:
                    # Committed but dst gone -- can't recover; surface.
                    conn.execute(
                        "UPDATE videos SET move_state=NULL, updated_at=? WHERE id=?",
                        (_iso_now(), video_id),
                    )
                    conn.execute(
                        "UPDATE move_log SET status='failed', finished_at=?, "
                        "error='recovery: committed but dst missing' WHERE id=?",
                        (_iso_now(), log_row["id"]),
                    )
                    counts["manual_review"] += 1
                continue

            src = Path(log_row["from_path"]) if log_row["from_path"] else None
            dst = Path(log_row["to_path"]) if log_row["to_path"] else None
            src_exists = bool(src and src.exists())
            dst_exists = bool(dst and dst.exists())

            if log_row["status"] == "started":
                # Cases A / B: copy didn't certifiably finish. Discard
                # any partial dst, leave src in place, and roll the DB
                # back to its from_project state.
                if dst_exists and src_exists:
                    shutil.rmtree(dst, ignore_errors=True)
                    dst_exists = False
                if src_exists:
                    conn.execute(
                        "UPDATE videos SET move_state=NULL, project_id=?, path=?, "
                        "updated_at=? WHERE id=?",
                        (
                            log_row["from_project"],
                            str(src),
                            _iso_now(),
                            video_id,
                        ),
                    )
                    conn.execute(
                        "UPDATE move_log SET status='rolled_back', finished_at=?, "
                        "error='recovery: source preserved; copy did not commit' "
                        "WHERE id=?",
                        (_iso_now(), log_row["id"]),
                    )
                    counts["rolled_back"] += 1
                    forget_video_path(video_id)
                else:
                    # Case E: neither side has a folder. The data is
                    # gone and we can't tell whether to point the row
                    # at the old or new project. Clear the latch and
                    # surface a warning; the operator decides next.
                    conn.execute(
                        "UPDATE videos SET move_state=NULL, updated_at=? WHERE id=?",
                        (_iso_now(), video_id),
                    )
                    conn.execute(
                        "UPDATE move_log SET status='failed', finished_at=?, "
                        "error='recovery: both src and dst missing; manual review needed' "
                        "WHERE id=?",
                        (_iso_now(), log_row["id"]),
                    )
                    log.warning(
                        "move recovery: video %s has no folder at either path; "
                        "from=%s to=%s -- manual review needed",
                        video_id, log_row["from_path"], log_row["to_path"],
                    )
                    counts["manual_review"] += 1
            elif log_row["status"] == "verified":
                # Cases C / D: the FS side committed; the DB write
                # didn't. Roll forward to the destination.
                if not dst_exists:
                    # Even verified, nothing on disk -- treat like case
                    # B and roll back if possible.
                    if src_exists:
                        conn.execute(
                            "UPDATE videos SET move_state=NULL, project_id=?, path=?, "
                            "updated_at=? WHERE id=?",
                            (
                                log_row["from_project"],
                                str(src),
                                _iso_now(),
                                video_id,
                            ),
                        )
                        conn.execute(
                            "UPDATE move_log SET status='rolled_back', finished_at=?, "
                            "error='recovery: verified but dst missing; rolled back to src' "
                            "WHERE id=?",
                            (_iso_now(), log_row["id"]),
                        )
                        counts["rolled_back"] += 1
                        forget_video_path(video_id)
                    else:
                        conn.execute(
                            "UPDATE videos SET move_state=NULL, updated_at=? WHERE id=?",
                            (_iso_now(), video_id),
                        )
                        conn.execute(
                            "UPDATE move_log SET status='failed', finished_at=?, "
                            "error='recovery: verified but neither path exists' "
                            "WHERE id=?",
                            (_iso_now(), log_row["id"]),
                        )
                        counts["manual_review"] += 1
                    continue
                # Case C: clean up the orphaned source (verified means
                # the dst is good).
                if src_exists:
                    shutil.rmtree(src, ignore_errors=True)
                conn.execute(
                    "UPDATE videos SET move_state=NULL, project_id=?, path=?, "
                    "updated_at=? WHERE id=?",
                    (
                        log_row["to_project"],
                        str(dst),
                        _iso_now(),
                        video_id,
                    ),
                )
                # Bump events_since_build the same way the happy-path
                # commit does, so a recovered move and a clean move
                # produce the same "graph badge" delta.
                conn.execute(
                    "UPDATE projects SET events_since_build = events_since_build + 1 "
                    "WHERE id IN (?, ?)",
                    (log_row["from_project"], log_row["to_project"]),
                )
                conn.execute(
                    "UPDATE move_log SET status='committed', finished_at=?, "
                    "error='recovery: rolled forward to dst' WHERE id=?",
                    (_iso_now(), log_row["id"]),
                )
                counts["recovered"] += 1
                forget_video_path(video_id)
    finally:
        conn.close()
    return counts
