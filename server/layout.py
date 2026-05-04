"""Per-video folder layout: every video lives under
`output/projects/<project_id>/<video_id>/` after migration 004 (the
folder-per-project layout). Pre-migration installations had a flat
`output/<video_id>/` layout; the helpers here transparently handle
both via a DB lookup with a flat-fallback for unmigrated rows.

Canonical per-video file names (unchanged across the migration):
    transcript.json   -- segments + metadata (authoritative)
    transcript.txt    -- plain text
    transcript.srt    -- subtitles
    audio.mp3         -- original audio
    CLAUDE.md         -- per-video Claude Code project guidance

Read paths go through `video_dir(out_dir, video_id)`, which reads
`videos.path` from the DB with an in-process LRU cache. New ingests
that don't have a DB row yet use `new_video_dir(out_dir, project_id,
video_id)` to compute the target path explicitly.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


PROJECTS_SUBDIR = "projects"
INBOX_PROJECT_ID = "inbox"


# In-process cache for (out_dir, video_id) -> Path resolutions. Hit rate
# is very high for the audio-streaming endpoint (one video opened, a
# thousand range requests against /api/transcripts/<id>/audio). Keyed on
# both the data root and the video id so a per-test tmp_path doesn't
# alias a previous test's resolution. Invalidated on moves via
# `forget_video_path()`.
_path_cache: dict[tuple[str, str], Path] = {}
_path_cache_lock = threading.Lock()


def _cache_key(out_dir: Path, video_id: str) -> tuple[str, str]:
    return (str(out_dir), video_id)


def project_dir(out_dir: Path, project_id: str) -> Path:
    """The on-disk folder for a project. All of a project's videos live
    inside it, plus a `graphify-out/` subfolder once the user builds a
    knowledge graph for that project."""
    return out_dir / PROJECTS_SUBDIR / project_id


def new_video_dir(out_dir: Path, project_id: str, video_id: str) -> Path:
    """Compute the folder a NEW video will live in. Used at ingest time
    before the videos row exists, since `video_dir()` would fail to
    resolve a path for a video that hasn't been persisted yet."""
    return project_dir(out_dir, project_id) / video_id


def video_dir(out_dir: Path, video_id: str) -> Path:
    """Folder for an existing video. Reads `videos.path` from the DB
    (cached). Falls back to the legacy flat layout (`out_dir / video_id`)
    for any row that doesn't yet have `path` populated -- so a server
    boot that predates the folder migration still works."""
    key = _cache_key(out_dir, video_id)
    with _path_cache_lock:
        cached = _path_cache.get(key)
        if cached is not None:
            return cached

    # Local import so the layout module doesn't pull in DB deps at
    # import time (keeps tests that mock the layout cheap).
    from .db import open_connection

    db_path = out_dir / "app.db"
    if not db_path.exists():
        # No DB at all -- pre-init or a bare test fixture. Use the
        # legacy flat layout so callers that just want a path get one.
        return out_dir / video_id

    conn = open_connection(db_path)
    try:
        row = conn.execute(
            "SELECT path FROM videos WHERE id=?", (video_id,)
        ).fetchone()
    except Exception:
        row = None
    finally:
        conn.close()

    if row and row["path"]:
        path = Path(row["path"])
        with _path_cache_lock:
            _path_cache[key] = path
        return path
    # Pre-migration row OR an unknown id. Return the legacy flat path
    # so callers that just need a Path get one, but DON'T cache it --
    # if this is a not-yet-persisted ingest, the row will appear with
    # the real path once persist_video_to_db runs, and a stale cache
    # entry would mask it forever (the bug surfaced as "Waiting for
    # transcript" persisting after a successful ingest).
    return out_dir / video_id


def forget_video_path(video_id: str | None = None) -> None:
    """Drop cached path entries for `video_id` across every out_dir
    (the cache is keyed on (out_dir, video_id) but invalidations come
    from move flows that don't necessarily know the out_dir). When
    `video_id` is None, clears the entire cache."""
    with _path_cache_lock:
        if video_id is None:
            _path_cache.clear()
            return
        for key in list(_path_cache.keys()):
            if key[1] == video_id:
                _path_cache.pop(key, None)


def transcript_json(out_dir: Path, video_id: str) -> Path:
    return video_dir(out_dir, video_id) / "transcript.json"


def transcript_txt(out_dir: Path, video_id: str) -> Path:
    return video_dir(out_dir, video_id) / "transcript.txt"


def transcript_srt(out_dir: Path, video_id: str) -> Path:
    return video_dir(out_dir, video_id) / "transcript.srt"


def audio_path(out_dir: Path, video_id: str) -> Path:
    return video_dir(out_dir, video_id) / "audio.mp3"


# Extensions that yt-dlp's FFmpegExtractAudio post-processor (or a direct
# audio-URL ingest before postprocessing) might drop into the per-video
# folder. Order is preferred-first.
_AUDIO_EXTS: tuple[str, ...] = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".webm")


def find_audio_file(out_dir: Path, video_id: str) -> Path | None:
    folder = video_dir(out_dir, video_id)
    if not folder.exists():
        return None
    for ext in _AUDIO_EXTS:
        p = folder / f"audio{ext}"
        if p.exists():
            return p
    try:
        for entry in folder.iterdir():
            if entry.is_file() and entry.name.startswith("audio."):
                return entry
    except OSError:
        pass
    return None


def claude_md(out_dir: Path, video_id: str) -> Path:
    return video_dir(out_dir, video_id) / "CLAUDE.md"


def iter_video_dirs(out_dir: Path):
    """Yield every video folder on disk under the new layout. Used by
    full-text search and any maintenance that needs to walk the corpus.
    Layout: `out_dir / projects / <project_id> / <video_id> /`."""
    projects_root = out_dir / PROJECTS_SUBDIR
    if not projects_root.is_dir():
        return
    for proj in projects_root.iterdir():
        if not proj.is_dir():
            continue
        for vid in proj.iterdir():
            if vid.is_dir():
                yield vid


# ---------------------------------------------------------------------------
# Per-video CLAUDE.md
# ---------------------------------------------------------------------------


def render_video_claude_md(meta: dict[str, Any]) -> str:
    title = meta.get("title") or meta.get("id", "video")
    duration = meta.get("duration_sec") or 0
    h, rem = divmod(int(duration), 3600)
    m, s = divmod(rem, 60)
    duration_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    speakers = sorted({seg.get("speaker") for seg in meta.get("segments", []) if seg.get("speaker")})
    speakers_str = ", ".join(speakers) if speakers else "single speaker (not diarized)"
    seg_count = len(meta.get("segments", []))
    lang = meta.get("language") or "?"
    url = meta.get("url") or "?"

    body = """# {title}

Per-video Claude Code project. You have a single transcript loaded as
context: `transcript.json` (also available as `transcript.txt` and
`transcript.srt`). Audio is in `audio.mp3` if you need it.

## This video

| | |
|---|---|
| Title       | {title} |
| Source      | {url} |
| Duration    | {duration_str} ({duration} sec) |
| Language    | {lang} |
| Segments    | {seg_count} |
| Speakers    | {speakers_str} |

## How to answer questions about this video

1. **Read `transcript.json`** for the full content. Each segment has `start`,
   `end`, `text`, and (when diarized) `speaker`.

2. **Search efficiently** with `jq`:
   ```bash
   jq '.segments[] | select(.text | test("rag"; "i")) | {{start, speaker, text}}' transcript.json
   ```
   Or for plain text:
   ```bash
   rg -i "rag" transcript.txt
   ```

3. **Cite every claim** with a timestamp:
   `[<HH:MM:SS>]` -- read `start` (seconds) and format as HH:MM:SS.
   With diarization: `SPEAKER_01 [00:14:22]: "..."`.

4. **Quote sparingly.** Direct quotes when they carry the answer; paraphrase
   otherwise.

5. **Don't speculate.** If the transcript doesn't say something, say so.
   "Not in the transcript" is a valid answer.

## Cross-video questions

This Claude session is scoped to *one video*. For questions across the
library, the user should re-launch from the library root (`output/`) or pop
out to a global session.
"""
    return body.format(
        title=title, url=url, duration_str=duration_str, duration=duration,
        lang=lang, seg_count=seg_count, speakers_str=speakers_str,
    )


# ---------------------------------------------------------------------------
# Legacy: flat-file -> per-video-folder migration (pre-folder-per-project)
# ---------------------------------------------------------------------------


def migrate_flat_outputs(out_dir: Path) -> dict[str, int]:
    """Move legacy `output/<id>.{json,txt,srt,mp3}` files into
    `output/<id>/{transcript.*, audio.mp3, CLAUDE.md}`. Idempotent.
    Predates the folder-per-project migration; kept for installations
    that haven't yet run either migration. New ingests don't need this.

    Returns counts: {migrated, already, skipped}.
    """
    counts = {"migrated": 0, "already": 0, "skipped": 0}
    if not out_dir.exists():
        return counts

    for json_file in list(out_dir.glob("*.json")):
        if not json_file.is_file():
            continue
        stem = json_file.stem
        if "." in stem:
            counts["skipped"] += 1
            continue
        video_id = stem
        target = out_dir / video_id
        if target.exists() and (target / "transcript.json").exists():
            counts["already"] += 1
            continue
        target.mkdir(parents=True, exist_ok=True)

        renames = [
            (json_file,                       target / "transcript.json"),
            (out_dir / f"{video_id}.txt",     target / "transcript.txt"),
            (out_dir / f"{video_id}.srt",     target / "transcript.srt"),
            (out_dir / f"{video_id}.mp3",     target / "audio.mp3"),
        ]
        for src, dst in renames:
            if src.exists() and not dst.exists():
                src.rename(dst)

        try:
            meta = json.loads((target / "transcript.json").read_text(encoding="utf-8"))
            (target / "CLAUDE.md").write_text(render_video_claude_md(meta), encoding="utf-8")
        except Exception:
            pass
        counts["migrated"] += 1
    return counts
