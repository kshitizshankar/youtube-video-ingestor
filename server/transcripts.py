"""List and read previously-generated transcript JSONs from per-video folders.

Archive model: each transcript.json carries an optional `archived: true`
flag. `list_transcripts` filters it out by default; `list_archived` returns
only the hidden ones. `set_archived` flips the flag; `delete_video` is
permanent and only meant to be called after soft-archiving.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .layout import transcript_json, video_dir


def _summary(json_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    # Attempt to read sibling meta.json for user-editable fields (tags, etc.)
    tags: list[str] = []
    meta_p = json_path.parent / "meta.json"
    if meta_p.exists():
        try:
            m = json.loads(meta_p.read_text(encoding="utf-8"))
            if isinstance(m.get("tags"), list):
                tags = [str(t) for t in m["tags"] if str(t).strip()]
        except Exception:
            pass
    # Fall back to computing speaker_count from segments if not explicit.
    sp_count = data.get("speaker_count")
    if sp_count is None:
        unique = {
            s.get("speaker") for s in (data.get("segments") or []) if s.get("speaker")
        }
        sp_count = len(unique)
    return {
        "id": data.get("id") or json_path.parent.name,
        "title": data.get("title") or json_path.parent.name,
        "duration_sec": data.get("duration_sec"),
        "language": data.get("language"),
        "diarized": data.get("diarized", False),
        "speaker_count": sp_count,
        "model": data.get("model"),
        "segment_count": len(data.get("segments", [])),
        "archived": bool(data.get("archived", False)),
        "tags": tags,
        "channel": data.get("channel"),
        "channel_url": data.get("channel_url"),
        "upload_date": data.get("upload_date"),
        "view_count": data.get("view_count"),
        "like_count": data.get("like_count"),
    }


def _scan(out_dir: Path) -> list[dict[str, Any]]:
    """Ordered scan of every transcript.json, most-recent first."""
    if not out_dir.exists():
        return []
    candidates: list[tuple[float, Path]] = []
    for sub in out_dir.iterdir():
        if not sub.is_dir():
            continue
        j = sub / "transcript.json"
        if not j.exists():
            continue
        try:
            mtime = j.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, j))
    candidates.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, Any]] = []
    for _, j in candidates:
        s = _summary(j)
        if s is not None:
            out.append(s)
    return out


def list_transcripts(out_dir: Path) -> list[dict[str, Any]]:
    """Live (non-archived) videos."""
    return [s for s in _scan(out_dir) if not s["archived"]]


def list_archived(out_dir: Path) -> list[dict[str, Any]]:
    """Soft-deleted videos. Still on disk, hidden from the main library."""
    return [s for s in _scan(out_dir) if s["archived"]]


def read_transcript(out_dir: Path, video_id: str) -> dict[str, Any] | None:
    p = transcript_json(out_dir, video_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def set_archived(out_dir: Path, video_id: str, archived: bool) -> bool:
    """Flip the `archived` flag on this video's transcript.json. Returns
    False if the video doesn't exist."""
    p = transcript_json(out_dir, video_id)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    data["archived"] = bool(archived)
    p.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return True


def delete_video(out_dir: Path, video_id: str) -> bool:
    """PERMANENTLY delete output/<video_id>/ — all files, audio, analysis.

    Callers must have already confirmed via the archive step. Returns False
    if the folder doesn't exist."""
    vd = video_dir(out_dir, video_id)
    if not vd.exists():
        return False
    shutil.rmtree(vd, ignore_errors=False)
    return True
