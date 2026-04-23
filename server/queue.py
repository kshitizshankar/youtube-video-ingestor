"""Two-stage ingest pipeline:

- `_download_executor` (MAX_CONCURRENT_DOWNLOADS, default 4) — yt-dlp I/O.
- `_transcribe_executor` (MAX_CONCURRENT_TRANSCRIPTIONS, default 1) — GPU-bound
  Whisper + diarize + write + persist.

Handoff: when a download task finishes, it submits a transcribe task to the
second pool and returns. The download slot frees up immediately, while the
transcribe task waits its turn for a GPU slot. This lets all N downloads fire
in parallel while the GPU processes them one (or two) at a time.

The single-video `stream_transcription` async generator used by
`/api/transcribe` (Detail.tsx) is **not** on this queue — it still runs the
whole pipeline in its own worker thread and emits SSE. Only
`api_bulk_ingest` (POST /api/ingests) goes through here.
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import state
from .transcriber import (
    TranscribeRequest,
    _run_plain,
    _run_post_diarize,
    download_audio,
    extract_video_id,
    persist_video_to_db,
    write_outputs,
)


log = logging.getLogger(__name__)


_MAX_DL = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS") or "4")
_MAX_GPU = int(os.environ.get("MAX_CONCURRENT_TRANSCRIPTIONS") or "1")

_download_executor = ThreadPoolExecutor(max_workers=_MAX_DL, thread_name_prefix="ingest-dl")
_transcribe_executor = ThreadPoolExecutor(max_workers=_MAX_GPU, thread_name_prefix="ingest-gpu")


class _CancelledMidRun(Exception):
    """Raised from inside the transcribe task to exit cleanly through the
    normal finally-block path when cancel is requested mid-run."""


def _heartbeat_loop(video_id: str, stop: threading.Event) -> None:
    """Bump state.last_event_at every 5s so the stall timer doesn't fire
    during long synchronous blocks (WhisperX align, diarize can go minutes
    without emitting a segment or phase event)."""
    while not stop.wait(5.0):
        try:
            state.update(video_id)  # no kwargs → just refresh last_event_at
        except Exception:  # pragma: no cover
            break


def _transcribe_task(
    video_id: str,
    audio_path: Path,
    info: dict,
    req: TranscribeRequest,
    out_dir: Path,
    hf_token: str | None,
    project_id: str | None = None,
) -> None:
    """GPU-bound stage. Runs Whisper + diarize + write outputs + persist.

    Runs inside `_transcribe_executor` — capped by MAX_CONCURRENT_TRANSCRIPTIONS.
    """
    # Cancel check at stage entry (before we even grab the heartbeat thread).
    if state.is_cancel_requested(video_id):
        state.update(video_id, phase="cancelled")
        state.finish(video_id, error="cancelled by user")
        return

    stop_hb = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop, args=(video_id, stop_hb), daemon=True,
        name=f"ingest-hb-{video_id[:8]}",
    )
    hb_thread.start()

    def tracked_push(event: str, data: dict) -> None:
        """Mirror the `stream_transcription` push pattern: translate inner
        events into state mutations. No SSE queue here — the bulk-ingest
        path uses /api/ingests polling, not a per-tab event stream."""
        if event == "phase":
            state.update(video_id, phase=data.get("phase") or data.get("message") or "working")
        elif event == "segment":
            state.segment_received(video_id, float(data.get("end", 0)))
        elif event in ("analyze_progress", "segment_update"):
            # Keep last_event_at fresh so stall detection doesn't fire.
            state.update(video_id)

    try:
        state.update(video_id, phase="transcribing")
        if state.is_cancel_requested(video_id):
            raise _CancelledMidRun()
        result = _run_plain(req, audio_path, info, tracked_push)

        if state.is_cancel_requested(video_id):
            raise _CancelledMidRun()
        diarized = _run_post_diarize(
            audio_path,
            result["segments"],
            tracked_push,
            hf_token or "",
            req.device,
        )
        result["diarized"] = diarized

        if state.is_cancel_requested(video_id):
            raise _CancelledMidRun()
        tracked_push("phase", {"phase": "writing", "message": "Writing output files..."})
        write_outputs(
            out_dir, video_id, info,
            req.model, req.compute_type,
            result["language"], result["language_probability"],
            result["diarized"], result["segments"], req.url,
            transcription_elapsed_sec=result.get("elapsed_sec"),
            batched=req.batched,
            batch_size=req.batch_size,
        )
        persist_video_to_db(out_dir, video_id, info, result, req)

        # Attach to the submitting project (if any). The bulk endpoint can't
        # do this at enqueue time because the videos row doesn't exist yet.
        if project_id:
            try:
                from . import projects as projects_mod
                added = projects_mod.add_videos(out_dir, project_id, [video_id])
                if added < 0:
                    log.warning(
                        "project %s no longer exists — skipping membership for %s",
                        project_id, video_id,
                    )
            except Exception:
                log.exception("failed to add %s to project %s", video_id, project_id)

        state.update(video_id, phase="done")
        state.finish(video_id)
    except _CancelledMidRun:
        state.update(video_id, phase="cancelled")
        state.finish(video_id, error="cancelled by user")
    except Exception as e:
        log.exception("transcribe task failed for %s", video_id)
        state.finish(video_id, error=f"{type(e).__name__}: {e}")
    finally:
        stop_hb.set()


def _download_task(
    req: TranscribeRequest,
    out_dir: Path,
    hf_token: str | None,
    preliminary_id: str,
    project_id: str | None = None,
) -> None:
    """I/O-bound stage. Downloads audio, rekeys state if yt-dlp resolves a
    different canonical id than our regex guess, then hands the job off to
    the GPU executor. Runs inside `_download_executor` — capped by
    MAX_CONCURRENT_DOWNLOADS.
    """
    video_id = preliminary_id
    try:
        if state.is_cancel_requested(video_id):
            state.update(video_id, phase="cancelled")
            state.finish(video_id, error="cancelled by user")
            return

        state.update(video_id, phase="downloading")
        audio_path, info = download_audio(req.url, out_dir)

        # yt-dlp gives us the authoritative id; rekey if we guessed wrong
        # (e.g. URL had playlist context and our regex pulled the list id).
        actual_id = info["id"]
        if actual_id != video_id:
            state.rekey(video_id, actual_id)
            video_id = actual_id

        state.update(
            video_id,
            phase="awaiting_gpu",
            title=info.get("title"),
            duration_sec=info.get("duration"),
        )

        if state.is_cancel_requested(video_id):
            state.update(video_id, phase="cancelled")
            state.finish(video_id, error="cancelled by user")
            return

        _transcribe_executor.submit(
            _transcribe_task, video_id, audio_path, info, req, out_dir, hf_token, project_id,
        )
    except Exception as e:
        log.exception("download task failed for %s", video_id)
        state.finish(video_id, error=f"{type(e).__name__}: {e}")


def enqueue_ingest(
    req: TranscribeRequest,
    out_dir: Path,
    hf_token: str | None,
    project_id: str | None = None,
) -> None:
    """Schedule a fresh ingest. Returns immediately.

    If `project_id` is given, the video is added to that project after
    transcription completes (not at enqueue time — the videos row doesn't
    exist until persist_video_to_db lands).

    The bulk endpoint (`api_bulk_ingest`) pre-seeds a `queued` state record
    before calling us so the job appears in /api/ingests before the download
    starts. Callers outside that path (direct calls, tests) don't need to
    seed anything — we do it here if we don't find an existing record.
    """
    preliminary_id = extract_video_id(req.url) or f"pending-{req.url[-11:]}"

    # Take over a pre-seeded `queued` record or begin a fresh one. Refuse to
    # double-enqueue if a record is already past the queued/starting boundary
    # (i.e. another worker is actively downloading/transcribing it).
    existing = state.get(preliminary_id)
    if existing is not None and not existing.done and (existing.phase or "") not in ("queued", "starting"):
        log.warning(
            "enqueue_ingest: %s already in phase %s — skipping",
            preliminary_id, existing.phase,
        )
        return
    if existing is None:
        state.begin(preliminary_id, url=req.url)
        state.update(preliminary_id, phase="queued")

    _download_executor.submit(
        _download_task, req, out_dir, hf_token, preliminary_id, project_id,
    )


def mark_queued_orphans() -> int:
    """Called at startup. Any state record still in `queued`, `starting`,
    `downloading`, or `awaiting_gpu` from a prior run belongs to a dead
    executor — mark it orphaned so the UI stops showing it as in-flight."""
    fixed = 0
    stale_phases = {"queued", "starting", "downloading", "awaiting_gpu"}
    for rec in state.list_active():
        if (rec.get("phase") or "") in stale_phases and not rec.get("done"):
            state.update(rec["id"], phase="orphaned (restart)")
            state.finish(rec["id"], error="orphaned (worker gone at restart)")
            fixed += 1
    return fixed


def on_shutdown() -> None:
    """Best-effort drain on server shutdown. ThreadPoolExecutor won't
    hard-kill in-flight downloads or transcriptions — that's the contract."""
    _download_executor.shutdown(wait=False, cancel_futures=True)
    _transcribe_executor.shutdown(wait=False, cancel_futures=True)
