"""Projects service: create / list / get / update / delete + video
membership.

Slugs are derived from the name, lowercase dashed, max 48 chars.
Collisions auto-suffix (-2, -3, ...) up to 1000. Slug is stable --
renaming doesn't change it.

Folder-per-project (migration 004): every video has exactly one project
via `videos.project_id`. The Inbox project (id='inbox',
system_kind='inbox') receives unprojected videos and is non-deletable.
Adding a video to a project moves its on-disk folder via the move
protocol in `server.moves`; the higher-level "add" API here is
preserved as a thin wrapper that delegates to the move helper.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .layout import project_dir


_SLUG_PUNCT = re.compile(r"[^a-z0-9]+")
INBOX_PROJECT_ID = "inbox"


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
        # Surfaces non-deletable status to the UI.
        "system_kind": row["system_kind"] if "system_kind" in row.keys() else None,
        # Graph state for the project page section.
        "graph_state": row["graph_state"] if "graph_state" in row.keys() else "never",
        "graph_built_at": row["graph_built_at"] if "graph_built_at" in row.keys() else None,
        "graph_node_count": row["graph_node_count"] if "graph_node_count" in row.keys() else None,
        "graph_edge_count": row["graph_edge_count"] if "graph_edge_count" in row.keys() else None,
        "events_since_build": int(row["events_since_build"]) if "events_since_build" in row.keys() and row["events_since_build"] is not None else 0,
    }


def ensure_inbox(out_dir: Path, conn: sqlite3.Connection | None = None) -> None:
    """Idempotent: create the Inbox system project + folder if missing.
    Called on server startup so a fresh install never has a window where
    a video can't be assigned somewhere."""
    c, owned = _ensure_conn(conn, out_dir)
    try:
        row = c.execute(
            "SELECT system_kind FROM projects WHERE id=?", (INBOX_PROJECT_ID,),
        ).fetchone()
        now = _iso_now()
        if row is None:
            c.execute(
                "INSERT INTO projects(id, name, description, system_kind, created_at, updated_at) "
                "VALUES (?, 'Inbox', NULL, 'inbox', ?, ?)",
                (INBOX_PROJECT_ID, now, now),
            )
        elif row["system_kind"] != "inbox":
            c.execute(
                "UPDATE projects SET system_kind='inbox' WHERE id=?",
                (INBOX_PROJECT_ID,),
            )
    finally:
        _close_if_owned(c, owned)
    # Folder side: create the directory so write_outputs can land here
    # without touching project state.
    project_dir(out_dir, INBOX_PROJECT_ID).mkdir(parents=True, exist_ok=True)


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
        # Create the on-disk folder so subsequent ingests can land here
        # immediately without a separate "first time setup" path.
        project_dir(out_dir, candidate).mkdir(parents=True, exist_ok=True)
        result = get_project(out_dir, candidate, conn=c)
        # get_project returns {"project": ..., "videos": ...}; we only
        # want the project summary back.
        return result["project"] if result else {"id": candidate, "name": name}
    finally:
        _close_if_owned(c, owned)


def list_projects(out_dir: Path, conn=None) -> list[dict[str, Any]]:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        rows = c.execute("""
            SELECT p.*,
                   COUNT(v.id)                       AS video_count,
                   COALESCE(SUM(v.duration_sec), 0) AS total_seconds,
                   MAX(v.updated_at)                AS last_activity
              FROM projects p
              LEFT JOIN videos v ON v.project_id = p.id AND v.archived = 0
             GROUP BY p.id
             ORDER BY (CASE WHEN p.system_kind = 'inbox' THEN 0 ELSE 1 END),
                      (CASE WHEN last_activity IS NULL THEN 1 ELSE 0 END),
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
                   (SELECT COUNT(*) FROM videos
                     WHERE project_id = p.id AND archived = 0) AS video_count,
                   (SELECT COALESCE(SUM(duration_sec), 0) FROM videos
                     WHERE project_id = p.id AND archived = 0) AS total_seconds,
                   (SELECT MAX(updated_at) FROM videos
                     WHERE project_id = p.id) AS last_activity
              FROM projects p WHERE p.id = ?
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
                "added_at": r["created_at"],  # post-migration "added_at" = video's created_at
            }
            for r in c.execute("""
                SELECT id, title, duration_sec, channel, archived, created_at
                  FROM videos
                 WHERE project_id = ? AND archived = 0
                 ORDER BY created_at DESC
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
    """Delete a project and its folder. Refuses to delete a system
    project (Inbox). Member videos must be moved to Inbox first via the
    move protocol; this function does NOT cascade-move them itself --
    the route handler is responsible for orchestrating that."""
    if project_id == INBOX_PROJECT_ID:
        raise ValueError("Cannot delete the Inbox project")
    c, owned = _ensure_conn(conn, out_dir)
    try:
        row = c.execute(
            "SELECT system_kind FROM projects WHERE id=?", (project_id,),
        ).fetchone()
        if row is None:
            return False
        if row["system_kind"]:
            raise ValueError(
                f"Cannot delete system project '{project_id}' (kind={row['system_kind']!r})"
            )
        # Refuse if any videos still belong to the project. Forces the
        # caller to handle membership before delete (move to Inbox).
        n = c.execute(
            "SELECT COUNT(*) AS n FROM videos WHERE project_id=?",
            (project_id,),
        ).fetchone()["n"]
        if n > 0:
            raise ValueError(
                f"Cannot delete project '{project_id}': {n} videos still belong to it. "
                "Move them to Inbox first."
            )
        cur = c.execute("DELETE FROM projects WHERE id=?", (project_id,))
        if cur.rowcount == 0:
            return False
    finally:
        _close_if_owned(c, owned)

    # Remove the on-disk folder if empty (a graphify-out/ subdir is OK
    # to delete with the project).
    pdir = project_dir(out_dir, project_id)
    if pdir.exists():
        try:
            shutil.rmtree(pdir)
        except OSError:
            # Non-fatal -- the project row is already gone. Log to the
            # caller's discretion via the return value.
            pass
    return True


def add_videos(
    out_dir: Path, project_id: str, video_ids: list[str], conn=None,
) -> int:
    """Move each listed video into `project_id`. Uses the move protocol
    (server.moves.move_video) so files are physically relocated.
    Returns count of successful moves, or -1 if the project doesn't
    exist."""
    from .moves import move_video, MoveError  # local import to break cycle

    c, owned = _ensure_conn(conn, out_dir)
    try:
        if c.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            return -1
    finally:
        _close_if_owned(c, owned)

    moved = 0
    for vid in video_ids:
        try:
            move_video(out_dir, vid, project_id)
            moved += 1
        except MoveError:
            # Already in the project, video doesn't exist, or move-in-
            # progress -- skip silently. The route handler can re-fetch
            # state to surface what actually happened.
            continue
    return moved


def remove_video(
    out_dir: Path, project_id: str, video_id: str, conn=None,
) -> bool:
    """Remove a video from a project = move it to Inbox. Returns True
    if a move happened (i.e. the video was actually in this project)."""
    from .moves import move_video, MoveError

    c, owned = _ensure_conn(conn, out_dir)
    try:
        row = c.execute(
            "SELECT project_id FROM videos WHERE id=?", (video_id,),
        ).fetchone()
        if row is None or row["project_id"] != project_id:
            return False
    finally:
        _close_if_owned(c, owned)

    try:
        move_video(out_dir, video_id, INBOX_PROJECT_ID)
        return True
    except MoveError:
        return False
