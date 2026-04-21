"""Holds a process-wide cache of Whisper / WhisperX models, plus a streaming
generator that pushes events suitable for SSE.

Outputs land in `output/<video_id>/` per-video folders.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from faster_whisper import BatchedInferencePipeline, WhisperModel
from yt_dlp import YoutubeDL

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


def download_audio(url: str, out_dir: Path) -> tuple[Path, dict[str, Any]]:
    """Download audio into output/<video_id>/audio.<ext>. Returns the .mp3 path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio/best",
        # Per-video subfolder; canonical filename "audio.<ext>".
        "outtmpl": str(out_dir / "%(id)s" / "audio.%(ext)s"),
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
        "quiet": True,
        "no_warnings": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = audio_path_for(out_dir, info["id"])
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
    meta = {
        "url": url,
        "id": info.get("id"),
        "title": info.get("title") or info.get("id"),
        "duration_sec": info.get("duration"),
        "language": language,
        "language_probability": language_probability,
        "model": model,
        "compute_type": compute_type,
        "diarized": diarized,
        "segments": segments,
    }
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
        try:
            push("phase", {"phase": "downloading", "message": "Downloading audio..."})
            audio_path, info = download_audio(req.url, out_dir)
            video_id = info["id"]
            push("downloaded", {
                "id": video_id,
                "title": info.get("title"),
                "duration_sec": info.get("duration"),
            })
            if req.diarize:
                if not hf_token:
                    raise RuntimeError("HUGGINGFACE_TOKEN required for diarization")
                result = _run_diarize(req, audio_path, info, push, hf_token)
            else:
                result = _run_plain(req, audio_path, info, push)

            push("phase", {"phase": "writing", "message": "Writing output files..."})
            files = write_outputs(
                out_dir, video_id, info,
                req.model, req.compute_type,
                result["language"], result["language_probability"],
                result["diarized"], result["segments"], req.url,
            )

            # Kick off Claude-powered analysis (summary / highlights / chapters).
            # We run inside the same worker thread so the SSE connection stays
            # open with a visible phase; skipped silently if claude CLI is missing.
            from .analyze import analyze_video
            push("phase", {
                "phase": "analyzing",
                "message": "Generating summary, highlights & chapters...",
            })
            analysis_ok = False
            try:
                analysis_path = analyze_video(out_dir, video_id)
                analysis_ok = analysis_path is not None
            except Exception as e:  # pragma: no cover — best-effort step
                push("phase", {
                    "phase": "analyzing",
                    "message": f"Analysis skipped: {type(e).__name__}",
                })

            duration = info.get("duration") or 0
            rt = (duration / result["elapsed_sec"]) if result["elapsed_sec"] > 0 and duration else 0
            push("done", {
                "id": video_id,
                "elapsed_sec": result["elapsed_sec"],
                "realtime_factor": rt,
                "duration_sec": duration,
                "files": {k: str(v.name) for k, v in files.items()},
                "analyzed": analysis_ok,
            })
        except Exception as e:
            push("error", {"message": f"{type(e).__name__}: {e}"})

    asyncio.create_task(asyncio.to_thread(worker))

    while True:
        msg = await queue.get()
        yield msg
        if msg["event"] in ("done", "error"):
            return
