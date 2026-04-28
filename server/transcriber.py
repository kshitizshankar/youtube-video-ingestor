"""Holds a process-wide cache of Whisper / WhisperX models, plus a streaming
generator that pushes events suitable for SSE.

Outputs land in `output/<video_id>/` per-video folders.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from faster_whisper import BatchedInferencePipeline, WhisperModel
from yt_dlp import YoutubeDL

from . import state


# ---------------------------------------------------------------------------
# Video-ID extraction (mirrors web/src/api.ts#extractVideoId)
# ---------------------------------------------------------------------------

_YT_ID_RE = re.compile(
    r"(?:"
    r"youtube\.com/watch\?v=|"
    r"youtu\.be/|"
    r"youtube\.com/embed/|"
    r"youtube\.com/v/|"
    r"youtube\.com/shorts/|"
    r"m\.youtube\.com/watch\?v=|"
    r"music\.youtube\.com/watch\?v="
    r")([A-Za-z0-9_-]{11})"
)
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_video_id(url: str) -> str | None:
    """Accept full YouTube URLs (watch / short / embed / shorts / mobile /
    music) and bare 11-char IDs. Returns None on anything else."""
    url = (url or "").strip()
    m = _YT_ID_RE.search(url)
    if m:
        return m.group(1)
    if _BARE_ID_RE.match(url):
        return url
    return None
from .layout import (
    audio_path as audio_path_for,
    claude_md as claude_md_for,
    render_video_claude_md,
    transcript_json,
    transcript_srt,
    transcript_txt,
    video_dir,
)


# ---------------------------------------------------------------------------
# Model cache
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelKey:
    name: str
    compute_type: str
    device: str


class ModelCache:
    def __init__(self) -> None:
        self._plain: dict[ModelKey, WhisperModel] = {}
        self._batched: dict[ModelKey, BatchedInferencePipeline] = {}
        self._whisperx_model: Any | None = None
        self._align_models: dict[str, tuple[Any, Any]] = {}
        self._diarize_pipeline: Any | None = None

    def get_plain(self, key: ModelKey) -> WhisperModel:
        if key not in self._plain:
            self._plain[key] = WhisperModel(
                key.name, device=key.device, compute_type=key.compute_type
            )
        return self._plain[key]

    def get_batched(self, key: ModelKey) -> BatchedInferencePipeline:
        if key not in self._batched:
            self._batched[key] = BatchedInferencePipeline(model=self.get_plain(key))
        return self._batched[key]

    def get_whisperx(self, name: str, compute_type: str, device: str):
        import whisperx
        if self._whisperx_model is None:
            self._whisperx_model = whisperx.load_model(
                name, device, compute_type=compute_type
            )
        return self._whisperx_model

    def get_align(self, language: str, device: str):
        import whisperx
        if language not in self._align_models:
            self._align_models[language] = whisperx.load_align_model(
                language_code=language, device=device
            )
        return self._align_models[language]

    def get_diarize(self, hf_token: str, device: str):
        from whisperx.diarize import DiarizationPipeline
        if self._diarize_pipeline is None:
            self._diarize_pipeline = DiarizationPipeline(
                model_name="pyannote/speaker-diarization-3.1",
                token=hf_token,
                device=device,
            )
        return self._diarize_pipeline


_cache = ModelCache()


def get_cache() -> ModelCache:
    return _cache


# ---------------------------------------------------------------------------
# Audio download — into output/<id>/audio.mp3
# ---------------------------------------------------------------------------


def _ytdlp_cookie_opts() -> dict[str, Any]:
    """Feed yt-dlp cookies from the user's installed browser (or an exported
    cookies.txt) to bypass YouTube's "are you a bot" challenge. Controlled
    via env:
        YTDLP_COOKIES_BROWSER = chrome|firefox|edge|brave|chromium|opera|vivaldi|safari
        YTDLP_COOKIES_FILE    = path to a cookies.txt exported from a browser
    """
    out: dict[str, Any] = {}
    cookie_file = (os.environ.get("YTDLP_COOKIES_FILE") or "").strip()
    browser = (os.environ.get("YTDLP_COOKIES_BROWSER") or "").strip().lower()
    if cookie_file:
        out["cookiefile"] = cookie_file
        return out
    if browser in {"chrome", "firefox", "edge", "brave", "chromium", "opera", "vivaldi", "safari"}:
        # yt-dlp expects a tuple (browser, profile, keyring, container).
        out["cookiesfrombrowser"] = (browser,)
    return out


def _safe_video_id(url: str) -> str:
    """Stable, Windows-safe folder name for any URL.

    YouTube URLs keep their canonical 11-char id so the per-video folder
    matches existing transcripts. For everything else (podcasts,
    direct MP3 URLs, etc.), we hash the URL to `aud-<12 hex>` -- short,
    deterministic so a re-ingest of the same URL re-uses the folder, and
    free of `?`, `=`, `&`, etc. that yt-dlp's generic extractor leaves
    in `info["id"]` after a CDN redirect.
    """
    yt = extract_video_id(url)
    if yt:
        return yt
    import hashlib
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"aud-{h}"


def download_audio(
    url: str,
    out_dir: Path,
    *,
    progress_hook=None,
) -> tuple[Path, dict[str, Any]]:
    """Download audio into output/<safe_id>/audio.<ext>. Returns the .mp3 path.

    `progress_hook`, if given, is registered with yt-dlp and called from
    yt-dlp's worker thread on every chunk. The hook receives the standard
    yt-dlp progress dict (`status`, `downloaded_bytes`, `total_bytes`,
    `eta`, `speed`, `filename`, ...). Used by `stream_transcription` to
    bump `state.last_event_at` so the live registry doesn't false-fire its
    "stalled" annotation during long downloads -- yt-dlp holds the GIL
    inside its C-backed network loop, so our threaded heartbeat can't
    fire reliably until the download finishes.

    The output folder name is precomputed from the URL via
    `_safe_video_id()` (NOT from yt-dlp's `info["id"]`). yt-dlp's generic
    extractor uses the post-redirect URL path -- often with `?query` --
    as the id, which lands as a literal path segment on Windows and
    triggers `ValueError(EINVAL)` before the download starts. By feeding
    yt-dlp a fixed `outtmpl` we sidestep that entirely. We then mutate
    `info["id"]` so downstream rekey + persistence sees the safe id.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_id = _safe_video_id(url)
    target_dir = out_dir / safe_id
    opts: dict[str, Any] = {
        "format": "bestaudio/best",
        # Fixed safe folder; canonical filename "audio.<ext>".
        "outtmpl": str(target_dir / "audio.%(ext)s"),
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
        "quiet": True,
        "no_warnings": True,
        # Tolerance for shaky upstream CDNs. Podcast hosts (Buzzsprout,
        # Megaphone, Libsyn) start dropping connections once they see a
        # burst of requests from one IP -- raising the per-socket timeout
        # and retry budget keeps a single ingest alive through transient
        # 5xx / RST / read-timeout from the CDN.
        "socket_timeout": 60,
        "retries": 10,
        "fragment_retries": 10,
        "retry_sleep_functions": {
            # Exponential backoff: 1s, 2s, 4s, ..., capped at 30s.
            "http": lambda n: min(30, 2 ** (n - 1)),
        },
    }
    if progress_hook is not None:
        # yt-dlp accepts a list -- multiple hooks can coexist if needed.
        opts["progress_hooks"] = [progress_hook]
    opts.update(_ytdlp_cookie_opts())
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # Replace yt-dlp's generic id with our safe folder name so the
        # caller's `state.rekey(old_id, info["id"])` lands on a stable id
        # that matches the on-disk folder. For YouTube URLs the safe id
        # equals what yt-dlp would have returned anyway.
        info["id"] = safe_id
        path = audio_path_for(out_dir, safe_id)
        return path, info


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        ms, s = 0, s + 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _extract_video_metadata(info: dict) -> dict[str, Any]:
    """Pluck the useful YouTube metadata yt-dlp gives us for free on every
    download. All fields optional — older videos that predate this capture
    will have nothing to show, and the UI degrades gracefully."""
    description = info.get("description") or ""
    return {
        "channel": info.get("channel") or info.get("uploader"),
        "channel_id": info.get("channel_id") or info.get("uploader_id"),
        "channel_url": info.get("channel_url") or info.get("uploader_url"),
        "channel_follower_count": info.get("channel_follower_count"),
        "upload_date": info.get("upload_date"),  # "YYYYMMDD"
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "description": description[:1000] if description else None,
        "categories": info.get("categories") or [],
        "yt_tags": info.get("tags") or [],  # YouTube's own tags, distinct from user tags
    }


def write_outputs(
    out_dir: Path,
    video_id: str,
    info: dict,
    model: str,
    compute_type: str,
    language: str | None,
    language_probability: float | None,
    diarized: bool,
    segments: list[dict],
    url: str,
    transcription_elapsed_sec: float | None = None,
    batched: bool | None = None,
    batch_size: int | None = None,
    metadata: dict | None = None,
) -> dict[str, Path]:
    vd = video_dir(out_dir, video_id)
    vd.mkdir(parents=True, exist_ok=True)

    text_lines = []
    srt_chunks = []
    for seg in segments:
        speaker = seg.get("speaker")
        prefix = f"{speaker}: " if speaker else ""
        text_lines.append(f"{prefix}{seg['text']}")
        srt_chunks.append(
            f"{seg['id']}\n{fmt_ts(seg['start'])} --> {fmt_ts(seg['end'])}\n{prefix}{seg['text']}\n"
        )

    txt_p = transcript_txt(out_dir, video_id)
    srt_p = transcript_srt(out_dir, video_id)
    json_p = transcript_json(out_dir, video_id)
    cmd_p = claude_md_for(out_dir, video_id)

    txt_p.write_text("\n".join(text_lines), encoding="utf-8")
    srt_p.write_text("\n".join(srt_chunks), encoding="utf-8")
    duration_sec = info.get("duration") or 0
    rt_factor: float | None = None
    if transcription_elapsed_sec and transcription_elapsed_sec > 0 and duration_sec:
        rt_factor = duration_sec / transcription_elapsed_sec

    unique_speakers = {
        s.get("speaker") for s in segments if s.get("speaker")
    }
    speaker_count = len(unique_speakers)

    is_podcast = bool(metadata) and (metadata or {}).get("source") == "podcast"
    if is_podcast:
        title = metadata.get("title") or info.get("title") or info.get("id")
        out_duration = metadata.get("duration_sec") or info.get("duration")
    else:
        title = info.get("title") or info.get("id")
        out_duration = info.get("duration")

    meta = {
        "url": url,
        "id": info.get("id"),
        "title": title,
        "duration_sec": out_duration,
        "language": language,
        "language_probability": language_probability,
        "model": model,
        "compute_type": compute_type,
        "diarized": diarized,
        "speaker_count": speaker_count,
        "transcription_elapsed_sec": transcription_elapsed_sec,
        "transcription_realtime_factor": rt_factor,
        "batched": batched,
        "batch_size": batch_size,
        **_extract_video_metadata(info),
        "segments": segments,
    }
    if is_podcast:
        # Override generic-extractor junk with the values the user saw in
        # the preview: host as "channel", episode/show description, the
        # podcast cover art, and the show metadata.
        meta["channel"] = metadata.get("host") or meta.get("channel")
        meta["channel_url"] = None
        if metadata.get("description"):
            meta["description"] = metadata["description"][:1000]
        meta["source"] = "podcast"
        meta["image_url"] = metadata.get("image_url")
        meta["show_name"] = metadata.get("show_name")
        meta["show_url"] = metadata.get("show_url")
        meta["pub_date"] = metadata.get("pub_date")
    else:
        meta["source"] = "youtube"
    json_p.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    cmd_p.write_text(render_video_claude_md(meta), encoding="utf-8")
    return {"txt": txt_p, "srt": srt_p, "json": json_p, "claude_md": cmd_p}


# ---------------------------------------------------------------------------
# Streaming entry point
# ---------------------------------------------------------------------------


@dataclass
class TranscribeRequest:
    url: str
    model: str = "distil-large-v3"
    compute_type: str = "int8_float16"
    device: str = "cuda"
    language: str | None = None
    beam_size: int = 5
    batched: bool = True
    batch_size: int = 16
    diarize: bool = False
    min_speakers: int | None = None
    max_speakers: int | None = None
    # Optional pre-supplied metadata for non-YouTube ingests (podcasts).
    # When present, the worker prefers these fields over yt-dlp's `info`
    # dict for title, description, channel/host, upload_date, duration,
    # image_url, show_name, show_url, and stamps source='podcast' on the
    # videos row. When None, behavior is unchanged (YouTube path).
    #
    # Shape:
    #   {
    #     "source":        "podcast",      # always "podcast" today
    #     "title":         str,
    #     "host":          str | None,
    #     "show_name":     str | None,
    #     "show_url":      str | None,
    #     "image_url":     str | None,
    #     "pub_date":      str | None,    # ISO-8601 from RSS
    #     "duration_sec":  float | None,
    #     "description":   str | None,
    #   }
    metadata: dict | None = None


def _emit(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, event: str, data: dict) -> None:
    payload = {"event": event, "data": json.dumps(data, ensure_ascii=False)}
    loop.call_soon_threadsafe(queue.put_nowait, payload)


def _run_plain(req: TranscribeRequest, audio_path: Path, info: dict, push) -> dict:
    key = ModelKey(req.model, req.compute_type, req.device)
    cache = get_cache()
    transcriber = cache.get_batched(key) if req.batched else cache.get_plain(key)
    push("phase", {"phase": "transcribing", "message": "Running Whisper..."})
    t0 = time.time()
    kwargs: dict[str, Any] = dict(language=req.language, vad_filter=True, beam_size=req.beam_size)
    if req.batched:
        kwargs["batch_size"] = req.batch_size
    seg_iter, t_info = transcriber.transcribe(str(audio_path), **kwargs)
    push("language", {"language": t_info.language, "probability": t_info.language_probability})

    segments = []
    for i, seg in enumerate(seg_iter, start=1):
        text = seg.text.strip()
        s = {"id": i, "start": float(seg.start), "end": float(seg.end), "text": text}
        segments.append(s)
        push("segment", s)

    elapsed = time.time() - t0
    return {
        "segments": segments,
        "language": t_info.language,
        "language_probability": t_info.language_probability,
        "elapsed_sec": elapsed,
        "diarized": False,
    }


def _run_post_diarize(
    audio_path: Path,
    segments: list[dict],
    push,
    hf_token: str,
    device: str,
) -> bool:
    """Run pyannote diarization on the already-transcribed audio and
    annotate `segments` in-place with speaker labels. Each segment gets the
    label of the pyannote turn that overlaps it the most.

    Returns True iff at least one segment got a speaker assigned.
    """
    if not hf_token:
        push("phase", {
            "phase": "diarizing",
            "message": "Skipping speakers — no HUGGINGFACE_TOKEN",
        })
        return False
    if not segments:
        return False

    push("phase", {"phase": "diarizing", "message": "Identifying speakers..."})

    try:
        import whisperx
        cache = get_cache()
        # Reuse the lazily-initialized pyannote pipeline from ModelCache.
        pipeline = cache.get_diarize(hf_token, device if device != "auto" else "cuda")

        audio = whisperx.load_audio(str(audio_path))
        diar = pipeline(audio)

        turns: list[tuple[float, float, str]] = []
        if hasattr(diar, "itertracks"):
            for turn, _, spk in diar.itertracks(yield_label=True):  # type: ignore[attr-defined]
                turns.append((float(turn.start), float(turn.end), str(spk)))
        elif hasattr(diar, "iterrows"):
            for _, row in diar.iterrows():  # type: ignore[attr-defined]
                turns.append((
                    float(row["start"]),
                    float(row["end"]),
                    str(row["speaker"]),
                ))

        if not turns:
            return False

        assigned = 0
        for seg in segments:
            s0, s1 = float(seg["start"]), float(seg["end"])
            best_sp: str | None = None
            best_overlap = 0.0
            for t0, t1, sp in turns:
                overlap = max(0.0, min(s1, t1) - max(s0, t0))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_sp = sp
            if best_sp:
                seg["speaker"] = best_sp
                assigned += 1
        return assigned > 0
    except Exception as e:
        log.warning("post-diarize failed: %s", e)
        push("phase", {
            "phase": "diarizing",
            "message": f"Speaker detection skipped: {type(e).__name__}",
        })
        return False


def _run_diarize(req: TranscribeRequest, audio_path: Path, info: dict, push, hf_token: str) -> dict:
    import whisperx
    cache = get_cache()
    device = req.device if req.device != "auto" else "cuda"

    push("phase", {"phase": "loading_whisperx", "message": "Loading WhisperX..."})
    model = cache.get_whisperx(req.model, req.compute_type, device)

    push("phase", {"phase": "transcribing", "message": "Transcribing..."})
    t0 = time.time()
    audio = whisperx.load_audio(str(audio_path))
    result = model.transcribe(audio, batch_size=req.batch_size, language=req.language)
    detected = result["language"]
    push("language", {"language": detected, "probability": None})

    push("phase", {"phase": "aligning", "message": "Word-level alignment..."})
    align_model, align_meta = cache.get_align(detected, device)
    result = whisperx.align(
        result["segments"], align_model, align_meta, audio, device,
        return_char_alignments=False,
    )

    push("phase", {"phase": "diarizing", "message": "Identifying speakers..."})
    diarize_pipeline = cache.get_diarize(hf_token, device)
    diarize_kwargs: dict[str, Any] = {}
    if req.min_speakers is not None:
        diarize_kwargs["min_speakers"] = req.min_speakers
    if req.max_speakers is not None:
        diarize_kwargs["max_speakers"] = req.max_speakers
    diarize_segments = diarize_pipeline(audio, **diarize_kwargs)
    result = whisperx.assign_word_speakers(diarize_segments, result)

    segments = []
    for i, seg in enumerate(result["segments"], start=1):
        s = {
            "id": i,
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "speaker": seg.get("speaker", "SPEAKER_??"),
            "text": seg["text"].strip(),
        }
        segments.append(s)
        push("segment", s)

    elapsed = time.time() - t0
    return {
        "segments": segments,
        "language": detected,
        "language_probability": None,
        "elapsed_sec": elapsed,
        "diarized": True,
    }


async def stream_transcription(
    req: TranscribeRequest, out_dir: Path, hf_token: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def push(event: str, data: dict) -> None:
        _emit(loop, queue, event, data)

    def worker() -> None:
        import threading

        # Register the job immediately so /api/ingests + Library strip know
        # it exists even if download fails. yt-dlp may resolve a different
        # canonical id later (e.g. URL had playlist context) — we rekey then.
        preliminary_id = extract_video_id(req.url) or f"pending-{uuid.uuid4().hex[:8]}"
        # Dedup vs take-over: the bulk /api/ingests endpoint pre-seeds a
        # `queued` record so the UI shows the job waiting for a worker slot.
        # When a worker picks us up here, that record is our own — don't
        # reject it. Only refuse if another transcription is actually past
        # the queued/starting boundary (phase like downloading/transcribing/etc).
        existing = state.get(preliminary_id)
        if existing is not None and not existing.done and (existing.phase or "") not in ("queued", "starting"):
            push("error", {
                "message": (
                    f"already running for {preliminary_id} — "
                    "wait for it to finish, or drop it from /api/ingests first"
                ),
            })
            return
        if existing is None:
            state.begin(preliminary_id, url=req.url)
        # Either way: mark `queued` (idempotent; a fresh begin starts there too).
        state.update(preliminary_id, phase="queued")
        video_id: str | None = preliminary_id

        # Shared mutable box for the heartbeat thread to peek at.
        hb_state = {"phase": "starting", "started": time.time()}
        stop_hb = threading.Event()

        def heartbeat() -> None:
            # Emit a named "heartbeat" SSE event every few seconds so the
            # client's stall detector doesn't fire while whisperx is stuck
            # inside a long synchronous block (transcribe / align / diarize
            # / analyze). Also bump the global state registry's last_event_at
            # so /api/ingests + the Library "stalled?" pill don't false-fire.
            while not stop_hb.wait(5.0):
                try:
                    if video_id:
                        state.update(video_id)  # no kwargs → just touches last_event_at
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "event": "heartbeat",
                        "data": json.dumps({
                            "phase": hb_state.get("phase") or "working",
                            "elapsed": time.time() - hb_state["started"],
                        }, ensure_ascii=False),
                    })
                except Exception:
                    break

        hb_thread = threading.Thread(target=heartbeat, daemon=True)
        hb_thread.start()

        # Cooperative cancel: raised from a check_cancel() helper so exit is
        # clean (finally-block runs, worker writes "cancelled" to state).
        class _Cancelled(Exception):
            pass

        def check_cancel() -> None:
            if video_id and state.is_cancel_requested(video_id):
                raise _Cancelled()

        try:
            check_cancel()
            hb_state["phase"] = "downloading"
            state.update(video_id, phase="downloading")
            push("phase", {"phase": "downloading", "message": "Downloading audio..."})

            # yt-dlp progress hook -- yt-dlp's network loop holds the GIL
            # for long stretches, so our 5s heartbeat thread can't run
            # reliably during a download. Each progress callback bumps
            # last_event_at directly (so list_active() doesn't mark this
            # stalled) and pushes a `progress` SSE event so the UI strip
            # shows live download bytes/percentage.
            def _ydl_progress(d: dict) -> None:
                if not video_id:
                    return
                # Always bump last_event_at, even on "finished" (the post-
                # processor still runs after this).
                state.update(video_id)
                status = d.get("status")
                if status == "downloading":
                    downloaded = d.get("downloaded_bytes") or 0
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    pct = (downloaded / total * 100) if total else None
                    payload = {
                        "phase": "downloading",
                        "downloaded_bytes": downloaded,
                        "total_bytes": total or None,
                        "speed": d.get("speed"),
                        "eta": d.get("eta"),
                    }
                    if pct is not None:
                        payload["pct"] = round(pct, 1)
                    push("download_progress", payload)
                elif status == "finished":
                    push("download_progress", {"phase": "downloaded", "pct": 100.0})

            audio_path, info = download_audio(req.url, out_dir, progress_hook=_ydl_progress)
            # yt-dlp gives us the authoritative id; rekey the registry entry
            # if our regex-based guess differed (e.g. playlist-context URL).
            actual_id = info["id"]
            if actual_id != video_id:
                state.rekey(video_id, actual_id)
                video_id = actual_id
            check_cancel()
            # Prefer pre-supplied podcast metadata over yt-dlp's generic-
            # extractor guesses (title gets populated with the CDN slug
            # otherwise).
            _meta_pre = req.metadata if isinstance(req.metadata, dict) else None
            _post_title = (
                (_meta_pre.get("title") if _meta_pre else None)
                or info.get("title")
            )
            _post_duration = (
                (_meta_pre.get("duration_sec") if _meta_pre else None)
                or info.get("duration")
            )
            state.update(
                video_id,
                phase="downloaded",
                title=_post_title,
                duration_sec=_post_duration,
            )
            push("downloaded", {
                "id": video_id,
                "title": _post_title,
                "duration_sec": _post_duration,
            })

            def run_and_track(fn, *args):
                """Wrap a stage so each yielded segment updates shared state."""
                return fn(*args)

            # Instrument the plain/diarize runners with a patched push that
            # also updates global state so cross-tab observers can see things.
            orig_push = push
            def tracked_push(event: str, data: dict) -> None:
                if event == "phase":
                    hb_state["phase"] = data.get("phase") or data.get("message") or "working"
                if video_id:
                    if event == "phase":
                        state.update(video_id, phase=hb_state["phase"])
                    elif event == "segment":
                        state.segment_received(video_id, float(data.get("end", 0)))
                    elif event in ("analyze_progress", "segment_update"):
                        # Don't change phase/segments, but DO prove the worker
                        # is alive — otherwise stall detection fires during
                        # analyze (no phase/segment events for 60-120s).
                        state.update(video_id)
                orig_push(event, data)

            check_cancel()
            # Always transcribe with faster-whisper (fast, streaming).
            result = _run_plain(req, audio_path, info, tracked_push)

            check_cancel()
            # Then run speaker diarization as a post-step. Works in both
            # live/batched modes, and gracefully skips when the HF token
            # isn't configured or pyannote fails.
            diarized = _run_post_diarize(
                audio_path,
                result["segments"],
                tracked_push,
                hf_token or "",
                req.device,
            )
            result["diarized"] = diarized
            # Re-emit annotated segments so the streaming UI shows speaker
            # tags that appeared after the initial transcription.
            if diarized:
                for s in result["segments"]:
                    if "speaker" in s:
                        tracked_push("segment_update", {
                            "id": s["id"],
                            "speaker": s["speaker"],
                        })

            tracked_push("phase", {"phase": "writing", "message": "Writing output files..."})
            files = write_outputs(
                out_dir, video_id, info,
                req.model, req.compute_type,
                result["language"], result["language_probability"],
                result["diarized"], result["segments"], req.url,
                transcription_elapsed_sec=result.get("elapsed_sec"),
                batched=req.batched,
                batch_size=req.batch_size,
                metadata=req.metadata,
            )
            persist_video_to_db(out_dir, video_id, info, result, req)

            duration = info.get("duration") or 0
            rt = (duration / result["elapsed_sec"]) if result["elapsed_sec"] > 0 and duration else 0
            state.update(video_id, phase="done")
            state.finish(video_id)
            push("done", {
                "id": video_id,
                "elapsed_sec": result["elapsed_sec"],
                "realtime_factor": rt,
                "duration_sec": duration,
                "files": {k: str(v.name) for k, v in files.items()},
            })
        except _Cancelled:
            if video_id:
                state.update(video_id, phase="cancelled")
                state.finish(video_id, error="cancelled by user")
            push("error", {"message": "cancelled by user"})
        except Exception as e:
            if video_id:
                state.finish(video_id, error=f"{type(e).__name__}: {e}")
            push("error", {"message": f"{type(e).__name__}: {e}"})
        finally:
            stop_hb.set()

    asyncio.create_task(asyncio.to_thread(worker))

    while True:
        msg = await queue.get()
        yield msg
        if msg["event"] in ("done", "error"):
            return


from datetime import datetime, timezone


def _iso_to_yyyymmdd(iso: str | None) -> str | None:
    """Convert an ISO-8601 timestamp (e.g. RSS pub_date) to YouTube-style
    YYYYMMDD. Returns None on any parse failure so callers can fall back."""
    if not iso:
        return None
    s = iso.strip()
    if not s:
        return None
    # datetime.fromisoformat handles "+11:00" but not bare "Z" until 3.11.
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%d")


def _folder_bytes(folder: Path) -> int:
    import os as _os
    import stat as _stat
    total = 0
    for root, _, files in _os.walk(folder, followlinks=False):
        for name in files:
            p = Path(root) / name
            try:
                st = p.lstat()
            except OSError:
                continue
            if _stat.S_ISLNK(st.st_mode):
                continue
            total += st.st_size
    return total


def persist_video_to_db(
    out_dir: Path,
    video_id: str,
    info: dict,
    result: dict,
    req: "TranscribeRequest",
) -> None:
    """Upsert a row in `videos` from the transcription outputs. Called
    right after write_outputs; failures here are logged but don't fail
    the stream (user still has their disk artifacts)."""
    import json as _json
    import logging
    log = logging.getLogger("server.transcriber.db")
    try:
        from .db import open_connection, run_migrations
        conn = open_connection(out_dir / "app.db")
    except Exception as e:
        log.warning("could not open DB to persist %s: %s", video_id, e)
        return
    try:
        run_migrations(conn)
        now = datetime.now(timezone.utc).isoformat()
        segments = result.get("segments") or []
        meta = req.metadata if isinstance(req.metadata, dict) else None
        is_podcast = bool(meta) and (meta or {}).get("source") == "podcast"

        # When metadata is supplied (podcast path), prefer it over yt-dlp's
        # `info` dict -- yt-dlp's generic extractor populates these fields
        # with junk for podcast CDN URLs (CDN slugs, generic placeholders).
        if is_podcast:
            title = meta.get("title") or info.get("title")
            channel = meta.get("host") or info.get("channel") or info.get("uploader")
            upload_date = (
                _iso_to_yyyymmdd(meta.get("pub_date"))
                or info.get("upload_date")
            )
            duration = meta.get("duration_sec") or info.get("duration")
            description_raw = meta.get("description") or info.get("description") or ""
            image_url = meta.get("image_url")
            show_name = meta.get("show_name")
            show_url = meta.get("show_url")
            source_val = "podcast"
        else:
            title = info.get("title")
            channel = info.get("channel") or info.get("uploader")
            upload_date = info.get("upload_date")
            duration = info.get("duration")
            description_raw = info.get("description") or ""
            image_url = None
            show_name = None
            show_url = None
            source_val = "youtube"

        row = {
            "id": video_id,
            "url": req.url,
            "title": title,
            "channel": channel,
            "channel_id": info.get("channel_id") or info.get("uploader_id"),
            "channel_url": info.get("channel_url") or info.get("uploader_url"),
            "channel_follower_count": info.get("channel_follower_count"),
            "upload_date": upload_date,
            "duration_sec": duration,
            "description": description_raw[:1000] or None,
            "categories": _json.dumps(info.get("categories") or []),
            "yt_tags": _json.dumps(info.get("tags") or []),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "comment_count": info.get("comment_count"),
            "language": result.get("language"),
            "language_probability": result.get("language_probability"),
            "diarized": 1 if result.get("diarized") else 0,
            "speaker_count": len({s.get("speaker") for s in segments if s.get("speaker")}),
            "segment_count": len(segments),
            "model": req.model,
            "compute_type": req.compute_type,
            "batched": 1 if req.batched else 0,
            "batch_size": req.batch_size,
            "transcription_elapsed_sec": result.get("elapsed_sec"),
            "transcription_realtime_factor": (
                (duration or 0) / result["elapsed_sec"]
                if result.get("elapsed_sec") else None
            ),
            "storage_bytes": _folder_bytes(out_dir / video_id),
            "transcribed_at": now,
            "updated_at": now,
            "source": source_val,
            "image_url": image_url,
            "show_name": show_name,
            "show_url": show_url,
        }
        conn.execute("""
            INSERT INTO videos(
                id, url, title, channel, channel_id, channel_url,
                channel_follower_count, upload_date, duration_sec, description,
                categories, yt_tags, view_count, like_count, comment_count,
                language, language_probability, diarized, speaker_count,
                segment_count, model, compute_type, batched, batch_size,
                transcription_elapsed_sec, transcription_realtime_factor,
                storage_bytes, archived, notes, owner, transcribed_at,
                created_at, updated_at,
                source, image_url, show_name, show_url
            ) VALUES (
                :id, :url, :title, :channel, :channel_id, :channel_url,
                :channel_follower_count, :upload_date, :duration_sec, :description,
                :categories, :yt_tags, :view_count, :like_count, :comment_count,
                :language, :language_probability, :diarized, :speaker_count,
                :segment_count, :model, :compute_type, :batched, :batch_size,
                :transcription_elapsed_sec, :transcription_realtime_factor,
                :storage_bytes, 0, NULL, NULL, :transcribed_at,
                :updated_at, :updated_at,
                :source, :image_url, :show_name, :show_url
            )
            ON CONFLICT(id) DO UPDATE SET
                url=excluded.url,
                title=excluded.title,
                channel=excluded.channel,
                channel_id=excluded.channel_id,
                channel_url=excluded.channel_url,
                channel_follower_count=excluded.channel_follower_count,
                upload_date=excluded.upload_date,
                duration_sec=excluded.duration_sec,
                description=excluded.description,
                categories=excluded.categories,
                yt_tags=excluded.yt_tags,
                view_count=excluded.view_count,
                like_count=excluded.like_count,
                comment_count=excluded.comment_count,
                language=excluded.language,
                language_probability=excluded.language_probability,
                diarized=excluded.diarized,
                speaker_count=excluded.speaker_count,
                segment_count=excluded.segment_count,
                model=excluded.model,
                compute_type=excluded.compute_type,
                batched=excluded.batched,
                batch_size=excluded.batch_size,
                transcription_elapsed_sec=excluded.transcription_elapsed_sec,
                transcription_realtime_factor=excluded.transcription_realtime_factor,
                storage_bytes=excluded.storage_bytes,
                transcribed_at=excluded.transcribed_at,
                updated_at=excluded.updated_at,
                source=excluded.source,
                image_url=excluded.image_url,
                show_name=excluded.show_name,
                show_url=excluded.show_url
        """, row)
    except Exception as e:
        log.warning("persist_video_to_db failed for %s: %s", video_id, e)
    finally:
        try: conn.close()
        except Exception: pass
