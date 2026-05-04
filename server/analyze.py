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


ANALYZE_PROMPT = r"""You are analyzing a video transcript to produce structured insights that help a viewer extract maximum value without re-watching.

Read `transcript.json` in the current directory. It contains segments with `{id, start, end, text}` (optionally `speaker`). Every timestamp you emit MUST be a real `start` value from a segment in that file — do not invent or round timestamps.

Output ONLY a single JSON object. No markdown, no code fences, no prose before or after. The object must match this schema:

{
  "value_prop": "1-3 sentences",
  "narrative_summary": "long multi-paragraph retelling",
  "takeaways": ["..."],
  "chapters": [
    { "start": 0, "title": "...", "note": "..." }
  ],
  "highlights": [
    {
      "start": 245, "end": 289,
      "speaker": "SPEAKER_00" | null,
      "quote": "verbatim substring from transcript",
      "reason": "why this is easy to miss / the non-obvious insight"
    }
  ],
  "check_screen_candidates": [
    {
      "t_sec": 421.3,
      "segment_id": 87,
      "trigger_text": "short phrase that triggered the flag",
      "signal": "deictic_reference" | "code_on_screen" | "diagram_drawn" | "matrix_math_shown" | "figure_reference" | "animation_or_transition",
      "what_i_expect_to_see": "short guess at what's on screen",
      "priority": "high" | "medium" | "low"
    }
  ]
}

## Goal

Surface genuine insights. **Do NOT compress.** The transcript is cheap; the value is the structure + commentary + flags you layer on top. Favor depth and fidelity over brevity. If a 90-minute video deserves ten paragraphs of narrative, write ten paragraphs. A TLDR here destroys exactly what we are trying to preserve.

## Field guide

### value_prop (1-3 sentences)
What a viewer walks away with from THIS video that they could not get from reading the notebook, a textbook, or a blog post on the same topic. The payoff. Concrete.

### narrative_summary (multi-paragraph, long)
A faithful story-like retelling of the video. Preserve the author's actual arc: how they motivate a problem, what they try first, what goes wrong, what aside they make halfway through, what they conclude and why. Keep the reasoning moves, side comments, and "why this, not that" asides. This is what a viewer reads to *internalize* the video without re-watching. Multiple paragraphs. Not a recap.

### takeaways (6-10)
Crisp, declarative, single-sentence lessons. The things a viewer should remember six months later.

### chapters (6-15)
Table of contents. First chapter MUST start at 0. Chapters cover the whole video in order, no gaps.
  - title: short, scannable
  - note: one sentence describing what actually happens in this section (not a summary of the whole video)

### highlights (10-20) — "What you might have missed"
These are **not** "why this moment stands out." They are the non-obvious gems — things a casual viewer would miss or mis-understand. Examples of what qualifies:
  - A subtle mathematical justification glossed over elsewhere
  - A gotcha the author calls out that you would only catch if paying attention
  - An aside that reframes the topic
  - A counterintuitive claim with a compact proof
  - A "why we don't do X" explanation

Each `reason` should be phrased as "easy to miss because ___" or "the non-obvious point is ___" — NOT "this is important because ___" (too generic).

`quote` must be a direct verbatim substring from a transcript segment's `text`.

### check_screen_candidates (0-30 per video; scale with length)
Transcript moments where the audio alone is insufficient — where a viewer would need to look at the screen. The transcript is text-only, so you are inferring this from the language.

**Flag when you see:**
  - Deictic references: "this", "here", "as you see", "right there", "over here"
  - Code being shown/run: "this cell", "let's run it", "notice the shape", "this line"
  - Diagrams drawn: "let me draw", "look at the diagram", "this arrow"
  - Matrix / tensor math shown: "this matrix", "these dimensions", "QK transpose"
  - Figure references: "Figure 3.2", "the illustration"
  - Animations/transitions: "watch what happens", "now when we…"

**Do NOT flag:**
  - Talking-head theory (no visual anchor in the phrase)
  - References to future sections ("we'll see later", "coming up")
  - Generic phrases with no visual anchor

**Per-candidate rules:**
- `t_sec` MUST equal a real `segment.start` in transcript.json.
- `segment_id` MUST equal that segment's `id`.
- `trigger_text` is the short phrase (from the segment's text) that triggered the flag.
- `signal` is the closest enum match from the list above.
- `what_i_expect_to_see` is a short guess: "the QK matrix heatmap", "a code cell showing `torch.matmul(q, k.T)`", "a diagram with orange attention heads".
- `priority`: high = audio alone is clearly insufficient; medium = audio gives most but screen adds precision; low = probably fine from audio but screen would confirm.

Skip the array entirely if the video is a talking head with no screen content.

## Constraints

- Output ONLY the JSON object. No prose, no backticks, no markdown fences.
- All timestamps must match actual `start` / `end` values in transcript.json.
- If the transcript is not diarized, set `highlights[].speaker` to null.
- If the transcript has no segments, return the object with empty arrays and empty strings — do not hallucinate content.
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
    """Persist parsed analysis to <video>/analyses/<id>.json and swap
    <video>/analysis.json to point at it (symlink with plain-copy fallback
    for Windows-without-permission). Returns the relative path."""
    from .layout import video_dir
    folder = video_dir(out_dir, video_id)
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
    from .layout import video_dir
    folder = video_dir(out_dir, video_id)
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
            # Auto-ingest check_screen_candidates → check_screen_marks.
            # Isolated try/except: a schema mismatch or a malformed list must
            # never fail the analysis run itself. Legacy analyses without the
            # field are naturally a no-op (None is valid input).
            try:
                from .marks import ingest_candidates as _ingest_marks
                _inserted = _ingest_marks(
                    out_dir,
                    video_id,
                    analysis_id,
                    final_result.get("check_screen_candidates"),
                )
                if _inserted:
                    log.info(
                        "analysis %s: inserted %d codex_suggested marks for %s",
                        analysis_id, _inserted, video_id,
                    )
            except Exception as _e:
                log.warning(
                    "check-screen candidate ingest failed for analysis %s: %s",
                    analysis_id, _e,
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
            # A fresh analysis means the project's graph is newly stale.
            try:
                proj_row = conn.execute(
                    "SELECT project_id FROM videos WHERE id=?", (video_id,),
                ).fetchone()
                if proj_row and proj_row["project_id"]:
                    from .graphify import bump_events_since_build
                    bump_events_since_build(out_dir, proj_row["project_id"])
            except Exception:
                log.debug(
                    "bump_events_since_build skipped for analysis %s",
                    analysis_id, exc_info=True,
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
        from .layout import video_dir
        return video_dir(out_dir, video_id) / row["file_path"]
    finally:
        conn.close()
