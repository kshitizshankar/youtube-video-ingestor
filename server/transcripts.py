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
]


def _summary_from_row(conn, row, *, project_ids: list[str] | None = None) -> dict:
    out = {k: row[k] for k in _SUMMARY_COLS}
    out["archived"] = bool(out["archived"])
    out["diarized"] = bool(out["diarized"])
    out["tags"] = [
        r["tag"] for r in conn.execute(
            "SELECT tag FROM video_tags WHERE video_id=? ORDER BY tag",
            (row["id"],),
        )
    ]
    out["project_ids"] = project_ids if project_ids is not None else []
    return out


def _bulk_project_ids(conn, video_ids: list[str]) -> dict[str, list[str]]:
    """One round-trip lookup of project memberships for a batch of videos."""
    if not video_ids:
        return {}
    placeholders = ",".join("?" * len(video_ids))
    rows = conn.execute(
        f"SELECT video_id, project_id FROM project_videos "
        f"WHERE video_id IN ({placeholders}) "
        f"ORDER BY video_id, project_id",
        video_ids,
    ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["video_id"], []).append(r["project_id"])
    return out


def _list_by_archived(conn, archived: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM videos WHERE archived=? ORDER BY created_at DESC",
        (archived,),
    ).fetchall()
    pid_map = _bulk_project_ids(conn, [r["id"] for r in rows])
    return [
        _summary_from_row(conn, r, project_ids=pid_map.get(r["id"], []))
        for r in rows
    ]


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
        cur = c.execute("DELETE FROM videos WHERE id=?", (video_id,))
        if cur.rowcount == 0:
            return False
    finally:
        _close_if_owned(c, owned)
    vd = video_dir(out_dir, video_id)
    if vd.exists():
        shutil.rmtree(vd, ignore_errors=False)
    return True
