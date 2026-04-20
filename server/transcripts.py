"""List and read previously-generated transcript JSONs from per-video folders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .layout import transcript_json


def _summary(json_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {
        "id": data.get("id") or json_path.parent.name,
        "title": data.get("title") or json_path.parent.name,
        "duration_sec": data.get("duration_sec"),
        "language": data.get("language"),
        "diarized": data.get("diarized", False),
        "model": data.get("model"),
        "segment_count": len(data.get("segments", [])),
    }


def list_transcripts(out_dir: Path) -> list[dict[str, Any]]:
    if not out_dir.exists():
        return []
    items = []
    # Each video lives in output/<id>/transcript.json. Sort by mtime descending.
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
    for _, j in candidates:
        s = _summary(j)
        if s is not None:
            items.append(s)
    return items


def read_transcript(out_dir: Path, video_id: str) -> dict[str, Any] | None:
    p = transcript_json(out_dir, video_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
