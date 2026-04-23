"""Post-transcribe analysis: dispatches through the AnalysisProvider
registry. Provider is user-selected at trigger time (no auto-run).

Each analysis run:
1. Inserts an `analyses` row (status='running').
2. Streams normalized events from the provider to the caller.
3. On done: writes parsed JSON to output/<video_id>/analyses/<id>.json and
   updates the row with finished_at/cost/tokens/file_path.
4. On error/cancel: updates the row with the outcome.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any, Iterator

from . import ai as ai_registry
from .ai.events import AnalysisEvent
from .db import open_connection, run_migrations


log = logging.getLogger(__name__)


ANALYZE_PROMPT = r"""Read `transcript.json` in the current directory and produce structured analysis.

Output ONLY a single JSON object. No markdown, no code fences, no prose before or after. The object must match this schema exactly:

{
  "summary": "2-3 sentence summary of what the video is about and why it matters",
  "takeaways": [
    "<short declarative takeaway>",
    "<another>"
  ],
  "chapters": [
    { "start": 0, "title": "<chapter title>" },
    { "start": 135, "title": "<next chapter>" }
  ],
  "highlights": [
    {
      "start": 245,
      "end": 289,
      "speaker": "SPEAKER_00",
      "quote": "<verbatim quote from the transcript>",
      "reason": "<one sentence: why this moment stands out>"
    }
  ]
}

Rules:
- 5 to 7 takeaways, each a single crisp sentence.
- 5 to 10 chapters. First chapter MUST start at 0. Chapters cover the whole video in order.
- 8 to 15 highlights. Each quote is a direct substring from the transcript's segments. Cite the segment's `start` in seconds.
- If the transcript is not diarized (segments have no `speaker` field), use null for speaker.
- All timestamps are integer or float seconds, matching actual `start` / `end` values in transcript.json.
- Output ONLY the JSON object, nothing else.
"""


# ---------------------------------------------------------------------------
# Cancel registry — module-level so a separate HTTP endpoint can signal
# a running analysis by id. Restart loses in-flight handles, which is fine.
# ---------------------------------------------------------------------------


_CANCELS: dict[int, Event] = {}


def cancel_analysis(analysis_id: int) -> bool:
    ev = _CANCELS.get(analysis_id)
    if ev is None:
        return False
    ev.set()
    return True


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_result_file(
    out_dir: Path, video_id: str, analysis_id: int, parsed: dict
) -> str:
    """Persist parsed analysis to output/<video>/analyses/<id>.json and
    swap output/<video>/analysis.json to point at it (symlink with plain-copy
    fallback for Windows-without-permission). Returns the relative path."""
    folder = out_dir / video_id
    analyses_dir = folder / "analyses"
    analyses_dir.mkdir(exist_ok=True)
    target = analyses_dir / f"{analysis_id}.json"
    parsed.setdefault("_meta", {})
    target.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")

    # Best-effort symlink swap at output/<video>/analysis.json for legacy consumers.
    link = folder / "analysis.json"
    tmp = link.with_name(link.name + ".link.tmp")
    try:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        os.symlink(target.name, tmp, target_is_directory=False)
        os.replace(tmp, link)
    except (OSError, NotImplementedError) as e:
        log.info("symlink swap unavailable (%s); falling back to plain copy", e)
        try:
            if tmp.exists() or tmp.is_symlink():
                tmp.unlink()
        except OSError:
            pass
        try:
            # Plain copy so the legacy endpoint still finds analysis.json.
            link.write_text(
                json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e2:
            log.warning("could not update analysis.json: %s", e2)

    return f"analyses/{analysis_id}.json"


# ---------------------------------------------------------------------------
# Streaming runner
# ---------------------------------------------------------------------------


def stream_analyze_video(
    out_dir: Path,
    video_id: str,
    *,
    provider_name: str = "claude_cli",
    model: str | None = None,
) -> Iterator[AnalysisEvent]:
    """Run analysis for `video_id` using the named provider, yielding
    normalized AnalysisEvent dicts. The outbound events are annotated with
    `analysis_id` so callers can correlate cancel requests."""
    folder = out_dir / video_id
    if not (folder / "transcript.json").exists():
        yield {"type": "error", "error_message": f"no transcript.json in {folder}"}
        return

    provider = ai_registry.get_provider(provider_name)
    if provider is None:
        yield {
            "type": "error",
            "error_message": f"unknown provider: {provider_name}",
        }
        return
    ok, reason = provider.available()
    if not ok:
        yield {
            "type": "error",
            "error_message": f"provider unavailable: {reason}",
        }
        return

    # Open DB + insert the running row up front so a cancel endpoint can find it.
    conn = open_connection(out_dir / "app.db")
    try:
        run_migrations(conn)
        # The analyses row FKs to videos(id); tests may pre-insert a row, but
        # the legacy path might not — skip silently if the FK fails and fall
        # back to a synthetic pseudo-id so the stream still works.
        started_at = _iso_now()
        try:
            cur = conn.execute(
                "INSERT INTO analyses (video_id, provider, model, status, started_at) "
                "VALUES (?, ?, ?, 'running', ?)",
                (video_id, provider_name, model, started_at),
            )
            analysis_id: int = int(cur.lastrowid)
        except Exception as e:
            yield {
                "type": "error",
                "error_message": f"could not record analysis run: {e}",
            }
            return
    finally:
        conn.close()

    cancel_event = Event()
    _CANCELS[analysis_id] = cancel_event

    last_usage: dict[str, Any] = {}
    final_result: dict[str, Any] | None = None
    error_message: str | None = None
    cancelled = False

    try:
        for evt in provider.stream_analyze(
            video_folder=folder,
            prompt=ANALYZE_PROMPT,
            model=model,
            cancel_event=cancel_event,
        ):
            if cancel_event.is_set():
                cancelled = True
                break
            t = evt.get("type")
            if t == "usage":
                for k in ("tokens_in", "tokens_out", "cost_usd", "cached_tokens"):
                    if k in evt:
                        last_usage[k] = evt[k]
            elif t == "done":
                final_result = evt.get("result")
                for k in ("tokens_in", "tokens_out", "cost_usd", "duration_ms"):
                    if k in evt:
                        last_usage[k] = evt[k]
            elif t == "error":
                error_message = evt.get("error_message") or "unknown error"

            # Annotate outbound so callers can correlate cancel / progress.
            evt_out: dict[str, Any] = dict(evt)
            evt_out["analysis_id"] = analysis_id
            yield evt_out  # type: ignore[misc]

            if t in ("done", "error"):
                break
        # Provider generator may have returned cleanly after cancel was
        # signalled, without yielding another event for our is_set() check
        # above to catch. Final test:
        if cancel_event.is_set() and final_result is None and error_message is None:
            cancelled = True
    finally:
        _CANCELS.pop(analysis_id, None)

    # Persist outcome.
    conn = open_connection(out_dir / "app.db")
    try:
        finished_at = _iso_now()
        if cancelled:
            conn.execute(
                "UPDATE analyses SET status='cancelled', finished_at=?, "
                "error='cancelled by user' WHERE id=?",
                (finished_at, analysis_id),
            )
            yield {
                "type": "error",
                "error_message": "cancelled by user",
                "analysis_id": analysis_id,  # type: ignore[typeddict-unknown-key]
            }
        elif error_message is not None:
            conn.execute(
                "UPDATE analyses SET status='error', finished_at=?, error=? WHERE id=?",
                (finished_at, error_message, analysis_id),
            )
        elif final_result is not None:
            # Embed metadata in the saved JSON.
            final_result["_meta"] = {
                "generated_at": finished_at,
                "provider": provider_name,
                "model": model,
                **{k: v for k, v in last_usage.items() if v is not None},
            }
            file_path = _write_result_file(
                out_dir, video_id, analysis_id, final_result
            )
            conn.execute(
                "UPDATE analyses SET status='done', finished_at=?, "
                "cost_usd=?, tokens_in=?, tokens_out=?, file_path=? WHERE id=?",
                (
                    finished_at,
                    last_usage.get("cost_usd"),
                    last_usage.get("tokens_in"),
                    last_usage.get("tokens_out"),
                    file_path,
                    analysis_id,
                ),
            )
        else:
            conn.execute(
                "UPDATE analyses SET status='error', finished_at=?, "
                "error='provider produced no result' WHERE id=?",
                (finished_at, analysis_id),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sync wrapper — drains the stream, returns the resulting file path.
# Kept for callers that don't want live progress.
# ---------------------------------------------------------------------------


def analyze_video(out_dir: Path, video_id: str, **kwargs) -> Path | None:
    last_file: str | None = None
    aid: int | None = None
    saw_error = False
    for evt in stream_analyze_video(out_dir, video_id, **kwargs):
        aid = evt.get("analysis_id") or aid  # type: ignore[assignment]
        if evt.get("type") == "done":
            last_file = evt.get("file_path") or None
        if evt.get("type") == "error":
            saw_error = True
    if saw_error and last_file is None:
        return None
    if aid is None:
        return None
    # Look up file_path from DB (authoritative).
    conn = open_connection(out_dir / "app.db")
    try:
        row = conn.execute(
            "SELECT file_path FROM analyses WHERE id=?", (aid,)
        ).fetchone()
        if not row or not row["file_path"]:
            return None
        return out_dir / video_id / row["file_path"]
    finally:
        conn.close()
