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


def _ensure_conn(conn, out_dir: Path):
    if conn is not None:
        return conn, False
    from .db import open_connection
    return open_connection(out_dir / "app.db"), True


def _close_if_owned(conn, owned: bool) -> None:
    if owned:
        conn.close()


_SUMMARY_COLS = [
    "id", "title", "duration_sec", "language", "diarized",
    "speaker_count", "model", "segment_count",
    "archived", "channel", "channel_url", "upload_date",
    "view_count", "like_count",
    # Source-aware columns. `source` is the discriminator the UI branches
    # on (audio player vs YouTube embed); `image_url` is the explicit
    # thumbnail for podcasts. show_name / show_url stay off the summary
    # to keep the cards row tight -- they surface in the full transcript.
    "source", "image_url",
    # Folder-per-project (migration 004): every video has exactly one
    # project_id (Inbox if the user didn't pick one).
    "project_id",
]


def _summary_from_row(conn, row) -> dict:
    out = {k: row[k] for k in _SUMMARY_COLS}
    out["archived"] = bool(out["archived"])
    out["diarized"] = bool(out["diarized"])
    out["tags"] = [
        r["tag"] for r in conn.execute(
            "SELECT tag FROM video_tags WHERE video_id=? ORDER BY tag",
            (row["id"],),
        )
    ]
    # Frontend still expects project_ids[] for backwards compat with the
    # m:m era. Single-element list now (or empty for an unmigrated row).
    out["project_ids"] = [out["project_id"]] if out.get("project_id") else []
    return out


def _list_by_archived(conn, archived: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM videos WHERE archived=? ORDER BY created_at DESC",
        (archived,),
    ).fetchall()
    return [_summary_from_row(conn, r) for r in rows]


def list_transcripts(out_dir: Path, conn=None) -> list[dict]:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        return _list_by_archived(c, 0)
    finally:
        _close_if_owned(c, owned)


def list_archived(out_dir: Path, conn=None) -> list[dict]:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        return _list_by_archived(c, 1)
    finally:
        _close_if_owned(c, owned)


def read_transcript(out_dir: Path, video_id: str, conn=None) -> dict | None:
    """Read canonical transcript.json from disk; layer DB row on top for
    any DB-only fields. Returns None when unknown everywhere."""
    p = transcript_json(out_dir, video_id)
    disk = None
    if p.exists():
        try:
            disk = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            disk = None
    c, owned = _ensure_conn(conn, out_dir)
    try:
        row = c.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if row is None and disk is None:
            return None
        base = dict(row) if row else {}
        if disk:
            base.update({k: v for k, v in disk.items() if k != "archived"})
            if row is not None:
                base["archived"] = bool(row["archived"])
        return base
    finally:
        _close_if_owned(c, owned)


def set_archived(out_dir: Path, video_id: str, archived: bool, conn=None) -> bool:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        cur = c.execute(
            "UPDATE videos SET archived=?, updated_at=datetime('now') WHERE id=?",
            (1 if archived else 0, video_id),
        )
        return cur.rowcount > 0
    finally:
        _close_if_owned(c, owned)


def delete_video(out_dir: Path, video_id: str, conn=None) -> bool:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        # Capture the on-disk path BEFORE deleting the row. After delete,
        # video_dir() falls back to the flat layout because there's no
        # row to read videos.path from, which would point at the wrong
        # folder for post-migration installs.
        vd = video_dir(out_dir, video_id)
        cur = c.execute("DELETE FROM videos WHERE id=?", (video_id,))
        if cur.rowcount == 0:
            return False
    finally:
        _close_if_owned(c, owned)
    # Bust the cache so a future call doesn't hand back a path to a
    # folder that's about to vanish.
    from .layout import forget_video_path
    forget_video_path(video_id)
    if vd.exists():
        shutil.rmtree(vd, ignore_errors=False)
    return True
