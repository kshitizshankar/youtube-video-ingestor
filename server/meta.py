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


def read_meta(out_dir: Path, video_id: str, conn=None) -> dict[str, Any]:
    c, owned = _ensure_conn(conn, out_dir)
    try:
        row = c.execute(
            "SELECT notes, updated_at FROM videos WHERE id=?", (video_id,)
        ).fetchone()
        if row is None:
            return _empty()
        tags = [
            r["tag"] for r in c.execute(
                "SELECT tag FROM video_tags WHERE video_id=? ORDER BY rowid",
                (video_id,),
            )
        ]
        speakers = {
            r["label"]: r["name"] for r in c.execute(
                "SELECT label, name FROM video_speakers WHERE video_id=?",
                (video_id,),
            )
        }
        notes = row["notes"] or ""
        # Only surface updated_at when meta-related content actually exists.
        # A freshly-ingested video with no user edits should look "empty".
        has_meta = bool(tags) or bool(speakers) or bool(notes)
        return {
            "tags": tags,
            "speaker_names": speakers,
            "notes": notes,
            "updated_at": row["updated_at"] if has_meta else None,
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
    conn=None,
) -> dict[str, Any]:
    """Merge `updates` into the video's meta tables. Returns the merged
    record. Unknown keys ignored. Only fields present in `updates` are
    modified — omitted keys preserve prior values."""
    c, owned = _ensure_conn(conn, out_dir)
    try:
        c.execute("BEGIN")
        try:
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
