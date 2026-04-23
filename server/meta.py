"""Per-video user-editable metadata: tags, renamed speakers, free-form notes.

Separate from transcript.json (which is canonical output from the transcription
pipeline and gets rewritten on every re-run) so user edits never get stomped.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .layout import video_dir


def meta_path(out_dir: Path, video_id: str) -> Path:
    return video_dir(out_dir, video_id) / "meta.json"


def _empty() -> dict[str, Any]:
    return {
        "tags": [],
        "speaker_names": {},
        "notes": "",
        "updated_at": None,
    }


def read_meta(out_dir: Path, video_id: str) -> dict[str, Any]:
    p = meta_path(out_dir, video_id)
    if not p.exists():
        return _empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return _empty()
    # Normalize shape so the caller can always rely on all keys.
    out = _empty()
    if isinstance(data.get("tags"), list):
        out["tags"] = [str(t).strip() for t in data["tags"] if str(t).strip()]
    if isinstance(data.get("speaker_names"), dict):
        out["speaker_names"] = {str(k): str(v) for k, v in data["speaker_names"].items()}
    if isinstance(data.get("notes"), str):
        out["notes"] = data["notes"]
    if isinstance(data.get("updated_at"), str):
        out["updated_at"] = data["updated_at"]
    return out


def write_meta(out_dir: Path, video_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Merge `updates` into meta.json. Only the keys we know about are
    accepted; everything else is ignored. Returns the merged record."""
    current = read_meta(out_dir, video_id)
    if "tags" in updates:
        tags = updates["tags"] or []
        if isinstance(tags, list):
            # Dedupe (preserve order) + strip.
            seen: set[str] = set()
            cleaned: list[str] = []
            for t in tags:
                s = str(t).strip()
                if s and s.lower() not in seen:
                    seen.add(s.lower())
                    cleaned.append(s)
            current["tags"] = cleaned
    if "speaker_names" in updates:
        sn = updates["speaker_names"] or {}
        if isinstance(sn, dict):
            # Keep only non-empty names; drop empty ones (treated as reset).
            current["speaker_names"] = {
                str(k): str(v).strip() for k, v in sn.items()
                if str(v).strip()
            }
    if "notes" in updates:
        if isinstance(updates["notes"], str):
            current["notes"] = updates["notes"]
    current["updated_at"] = datetime.now(timezone.utc).isoformat()

    p = meta_path(out_dir, video_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(current, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return current
