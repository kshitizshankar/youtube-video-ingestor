"""Projects service: create/list/get/rename/delete + video membership.

Slugs are derived from the name, lowercase dashed, max 48 chars. Collisions
auto-suffix (-2, -3, ...). Slug is stable — renaming doesn't change it.
"""
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
                if suffix > 1000:
                    raise
        return get_project(out_dir, candidate, conn=c)["project"]
    finally:
        _close_if_owned(c, owned)


def list_projects(out_dir: Path, conn=None) -> list[dict[str, Any]]:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        rows = c.execute("""
            SELECT p.*,
                   COUNT(pv.video_id)                AS video_count,
                   COALESCE(SUM(v.duration_sec), 0) AS total_seconds,
                   MAX(pv.added_at)                  AS last_activity
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


def get_project(out_dir: Path, project_id: str, conn=None) -> dict[str, Any] | None:
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
    out_dir: Path, project_id: str, updates: dict[str, Any], conn=None,
) -> bool:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        sets: list[str] = []
        vals: list[Any] = []
        if "name" in updates:
            sets.append("name=?"); vals.append(updates["name"])
        if "description" in updates:
            sets.append("description=?"); vals.append(updates["description"])
        if not sets:
            return True
        sets.append("updated_at=?"); vals.append(_iso_now())
        vals.append(project_id)
        cur = c.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE id=?", vals
        )
        return cur.rowcount > 0
    finally:
        _close_if_owned(c, owned)


def delete_project(out_dir: Path, project_id: str, conn=None) -> bool:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        cur = c.execute("DELETE FROM projects WHERE id=?", (project_id,))
        return cur.rowcount > 0
    finally:
        _close_if_owned(c, owned)


def add_videos(
    out_dir: Path, project_id: str, video_ids: list[str], conn=None,
) -> int:
    """Returns number of successful adds, or -1 if project not found."""
    c, owned = _ensure_conn(conn, out_dir)
    try:
        if c.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            return -1
        now = _iso_now()
        added = 0
        for vid in video_ids:
            if c.execute("SELECT 1 FROM videos WHERE id=?", (vid,)).fetchone() is None:
                continue
            c.execute(
                "INSERT OR IGNORE INTO project_videos(project_id, video_id, added_at) VALUES(?, ?, ?)",
                (project_id, vid, now),
            )
            added += 1
        return added
    finally:
        _close_if_owned(c, owned)


def remove_video(
    out_dir: Path, project_id: str, video_id: str, conn=None,
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
