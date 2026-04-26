"""Check-screen marks service + FastAPI router.

Marks are annotations attached to transcript moments that flag "the
audio/transcript alone is insufficient here; look at the screen." Two
populations coexist in one table:

- `codex_suggested` — inserted automatically when an AI analysis completes
  and produces a `check_screen_candidates` array. Carries the rich metadata
  (signal, what_i_expect_to_see, priority) that the model emitted.
- `user_marked` / `user_confirmed` / `dismissed` — user-driven state.
  `user_marked` is a fresh user mark. `user_confirmed` is the upgraded
  state of a `codex_suggested` row the user accepted. `dismissed` tombstones
  an AI suggestion the user rejected so it doesn't come back.

Cross-cutting note: the UNIQUE(video_id, t_sec, analysis_id) constraint
dedupes AI re-runs (same analysis_id + same timestamp = ignored). User
marks have analysis_id=NULL; SQLite treats NULLs as distinct, so users
can create multiple marks at the same timestamp across different segments
without colliding.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response

from . import auth
from .db import open_connection, run_migrations


log = logging.getLogger(__name__)


VALID_KINDS = {"codex_suggested", "user_marked", "user_confirmed", "dismissed"}
ACTIVE_KINDS = {"codex_suggested", "user_marked", "user_confirmed"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "video_id": row["video_id"],
        "t_sec": float(row["t_sec"]),
        "segment_id": int(row["segment_id"]) if row["segment_id"] is not None else None,
        "kind": row["kind"],
        "trigger_text": row["trigger_text"],
        "signal": row["signal"],
        "what_i_expect_to_see": row["what_i_expect_to_see"],
        "priority": row["priority"],
        "note": row["note"],
        "analysis_id": int(row["analysis_id"]) if row["analysis_id"] is not None else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _video_exists(conn: sqlite3.Connection, video_id: str) -> bool:
    r = conn.execute("SELECT 1 FROM videos WHERE id=?", (video_id,)).fetchone()
    return r is not None


# ---------------------------------------------------------------------------
# Auto-ingestion (called from analyze.py on a successful run)
# ---------------------------------------------------------------------------


def ingest_candidates(
    out_dir: Path,
    video_id: str,
    analysis_id: int,
    candidates: list[dict[str, Any]] | None,
) -> int:
    """Insert the AI-emitted `check_screen_candidates` as codex_suggested
    marks. Returns the number of rows newly inserted. `INSERT OR IGNORE`
    makes this idempotent against a UNIQUE(video_id, t_sec, analysis_id)
    constraint, so re-running an analysis is safe.

    Wrapped in defensive try/except at the call site — never fails an analysis.
    """
    if not candidates:
        return 0
    conn = open_connection(out_dir / "app.db")
    inserted = 0
    try:
        # First-run safety: ensure the marks table exists. Cheap (no-op on
        # already-applied migrations) but lets this work on a DB that hasn't
        # had any /api/videos/.../marks router endpoints hit yet.
        run_migrations(conn)
        now = _iso_now()
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            t_sec = cand.get("t_sec")
            if t_sec is None:
                continue
            try:
                t_sec_f = float(t_sec)
            except (TypeError, ValueError):
                continue
            seg_id = cand.get("segment_id")
            try:
                seg_id_i = int(seg_id) if seg_id is not None else None
            except (TypeError, ValueError):
                seg_id_i = None
            priority = cand.get("priority")
            if priority not in ("high", "medium", "low"):
                priority = None
            cur = conn.execute(
                "INSERT OR IGNORE INTO check_screen_marks "
                "(video_id, t_sec, segment_id, kind, trigger_text, signal, "
                " what_i_expect_to_see, priority, note, analysis_id, "
                " created_at, updated_at) "
                "VALUES (?, ?, ?, 'codex_suggested', ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    video_id,
                    t_sec_f,
                    seg_id_i,
                    cand.get("trigger_text"),
                    cand.get("signal"),
                    cand.get("what_i_expect_to_see"),
                    priority,
                    int(analysis_id),
                    now,
                    now,
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
    finally:
        conn.close()
    return inserted


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def build_router(out_dir: Path) -> APIRouter:
    """Build the marks router bound to a specific output directory.
    Returned router must be registered on the FastAPI app."""
    router = APIRouter(prefix="/api/videos", tags=["check-screen-marks"])

    @router.get(
        "/{video_id}/marks",
        dependencies=[Depends(auth.require_http)],
    )
    def list_marks(
        video_id: str,
        kind: str | None = Query(None),
        active_only: bool = Query(False),
    ) -> list[dict[str, Any]]:
        if kind is not None and kind not in VALID_KINDS:
            raise HTTPException(status_code=400, detail=f"invalid kind: {kind}")
        conn = open_connection(out_dir / "app.db")
        try:
            run_migrations(conn)
            if not _video_exists(conn, video_id):
                raise HTTPException(status_code=404, detail="video not found")
            sql = "SELECT * FROM check_screen_marks WHERE video_id=?"
            args: list[Any] = [video_id]
            if kind is not None:
                sql += " AND kind=?"
                args.append(kind)
            if active_only:
                # SQLite doesn't accept a Python set directly; expand inline.
                placeholders = ",".join(["?"] * len(ACTIVE_KINDS))
                sql += f" AND kind IN ({placeholders})"
                args.extend(sorted(ACTIVE_KINDS))
            sql += " ORDER BY t_sec ASC, id ASC"
            rows = conn.execute(sql, args).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()

    @router.post(
        "/{video_id}/marks",
        dependencies=[Depends(auth.require_http)],
    )
    def create_mark(
        video_id: str,
        body: dict[str, Any] = Body(...),
        response: Response = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be an object")
        t_sec = body.get("t_sec")
        if t_sec is None:
            raise HTTPException(status_code=400, detail="t_sec is required")
        try:
            t_sec_f = float(t_sec)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="t_sec must be a number")
        seg_id = body.get("segment_id")
        try:
            seg_id_i = int(seg_id) if seg_id is not None else None
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="segment_id must be an integer or null"
            )
        note = body.get("note")
        if note is not None and not isinstance(note, str):
            raise HTTPException(status_code=400, detail="note must be a string")

        conn = open_connection(out_dir / "app.db")
        try:
            run_migrations(conn)
            if not _video_exists(conn, video_id):
                raise HTTPException(status_code=404, detail="video not found")
            now = _iso_now()
            cur = conn.execute(
                "INSERT INTO check_screen_marks "
                "(video_id, t_sec, segment_id, kind, note, created_at, updated_at) "
                "VALUES (?, ?, ?, 'user_marked', ?, ?, ?)",
                (video_id, t_sec_f, seg_id_i, note, now, now),
            )
            new_id = int(cur.lastrowid)
            row = conn.execute(
                "SELECT * FROM check_screen_marks WHERE id=?", (new_id,)
            ).fetchone()
            if response is not None:
                response.status_code = 201
            return _row_to_dict(row)
        finally:
            conn.close()

    @router.patch(
        "/{video_id}/marks/{mark_id}",
        dependencies=[Depends(auth.require_http)],
    )
    def update_mark(
        video_id: str,
        mark_id: int,
        body: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be an object")
        sets: list[str] = []
        args: list[Any] = []
        if "kind" in body:
            k = body["kind"]
            if k not in VALID_KINDS:
                raise HTTPException(
                    status_code=400, detail=f"invalid kind: {k}"
                )
            sets.append("kind=?")
            args.append(k)
        if "note" in body:
            n = body["note"]
            if n is not None and not isinstance(n, str):
                raise HTTPException(status_code=400, detail="note must be a string or null")
            sets.append("note=?")
            args.append(n)
        if not sets:
            raise HTTPException(status_code=400, detail="nothing to update")
        sets.append("updated_at=?")
        args.append(_iso_now())

        conn = open_connection(out_dir / "app.db")
        try:
            run_migrations(conn)
            if not _video_exists(conn, video_id):
                raise HTTPException(status_code=404, detail="video not found")
            existing = conn.execute(
                "SELECT id FROM check_screen_marks WHERE id=? AND video_id=?",
                (mark_id, video_id),
            ).fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="mark not found")
            args_with_id = list(args) + [mark_id, video_id]
            conn.execute(
                f"UPDATE check_screen_marks SET {', '.join(sets)} "
                "WHERE id=? AND video_id=?",
                args_with_id,
            )
            row = conn.execute(
                "SELECT * FROM check_screen_marks WHERE id=?", (mark_id,)
            ).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    @router.delete(
        "/{video_id}/marks/{mark_id}",
        status_code=204,
        dependencies=[Depends(auth.require_http)],
    )
    def delete_mark(video_id: str, mark_id: int) -> Response:
        conn = open_connection(out_dir / "app.db")
        try:
            run_migrations(conn)
            cur = conn.execute(
                "DELETE FROM check_screen_marks WHERE id=? AND video_id=?",
                (mark_id, video_id),
            )
            if cur.rowcount == 0:
                # Distinguish "no video" vs "no mark" for a better error.
                if not _video_exists(conn, video_id):
                    raise HTTPException(status_code=404, detail="video not found")
                raise HTTPException(status_code=404, detail="mark not found")
            return Response(status_code=204)
        finally:
            conn.close()

    return router
