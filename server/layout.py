"""Per-video folder layout: each video lives in `output/<video_id>/` with a
canonical file naming scheme.

Layout:
    output/
        <video_id>/
            transcript.json   — segments + metadata (authoritative)
            transcript.txt    — plain text
            transcript.srt    — subtitles
            audio.mp3         — original audio
            CLAUDE.md         — per-video Claude Code project guidance
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def video_dir(out_dir: Path, video_id: str) -> Path:
    return out_dir / video_id


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
# folder. Order is preferred-first: mp3 is the post-processed canonical
# output; the others appear when the post-processor was skipped or when
# we were handed a non-mp3 source.
_AUDIO_EXTS: tuple[str, ...] = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".webm")


def find_audio_file(out_dir: Path, video_id: str) -> Path | None:
    """Return the first existing `audio.<ext>` in the per-video folder, or
    None if no audio sidecar is present. Used by the audio-streaming
    endpoint, which must serve whichever extension yt-dlp actually wrote."""
    folder = video_dir(out_dir, video_id)
    if not folder.exists():
        return None
    for ext in _AUDIO_EXTS:
        p = folder / f"audio{ext}"
        if p.exists():
            return p
    # Last-ditch: anything starting with "audio." (covers exotic codecs).
    try:
        for entry in folder.iterdir():
            if entry.is_file() and entry.name.startswith("audio."):
                return entry
    except OSError:
        pass
    return None


def claude_md(out_dir: Path, video_id: str) -> Path:
    return video_dir(out_dir, video_id) / "CLAUDE.md"


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
   `[<HH:MM:SS>]` — read `start` (seconds) and format as HH:MM:SS.
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
# One-time migration of flat-file outputs to per-video folders
# ---------------------------------------------------------------------------


def migrate_flat_outputs(out_dir: Path) -> dict[str, int]:
    """Move legacy `output/<id>.{json,txt,srt,mp3}` files into
    `output/<id>/{transcript.*, audio.mp3, CLAUDE.md}`. Idempotent.

    Returns counts: {migrated, already, skipped}.
    """
    counts = {"migrated": 0, "already": 0, "skipped": 0}
    if not out_dir.exists():
        return counts

    for json_file in list(out_dir.glob("*.json")):
        if not json_file.is_file():
            continue
        # Skip variant / debug files like `<id>.distil.txt` (have multi-dot stems)
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

        # Best-effort per-video CLAUDE.md
        try:
            meta = json.loads((target / "transcript.json").read_text(encoding="utf-8"))
            (target / "CLAUDE.md").write_text(render_video_claude_md(meta), encoding="utf-8")
        except Exception:
            pass
        counts["migrated"] += 1
    return counts
