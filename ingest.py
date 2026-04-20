"""Download a YouTube video's audio and transcribe it locally with faster-whisper.

Adds optional speaker diarization via WhisperX (--diarize), which needs a
HuggingFace token in .env (HUGGINGFACE_TOKEN=hf_...) and one-time license
acceptance for pyannote/segmentation-3.0 and pyannote/speaker-diarization-3.1.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


# CTranslate2 on Windows needs cuBLAS / cuDNN / CUDA-runtime DLLs on the loader path.
# We installed them via nvidia-*-cu12 wheels — prepend each bin dir to PATH AND register
# it with the loader so transitive DLL deps (e.g. cublas -> cudart) resolve correctly.
if sys.platform == "win32":
    for _pkg in (
        "nvidia.cublas",
        "nvidia.cudnn",
        "nvidia.cuda_runtime",
        "nvidia.cuda_nvrtc",
    ):
        _spec = importlib.util.find_spec(_pkg)
        if _spec and _spec.submodule_search_locations:
            _bin = Path(_spec.submodule_search_locations[0]) / "bin"
            if _bin.exists():
                os.add_dll_directory(str(_bin))
                os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")


from faster_whisper import BatchedInferencePipeline, WhisperModel  # noqa: E402
from yt_dlp import YoutubeDL  # noqa: E402


def download_audio(url: str, out_dir: Path) -> tuple[Path, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
        "quiet": False,
        "no_warnings": False,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        audio_path = out_dir / f"{info['id']}.mp3"
    return audio_path, info


def fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        ms, s = 0, s + 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe_plain(audio_path: Path, args) -> dict:
    """Run faster-whisper (optionally batched). Returns a dict with segments + meta."""
    mode = f"batched(bs={args.batch_size})" if args.batched else "sequential"
    print(f"[2/3] Loading model: {args.model} ({args.compute_type} on {args.device}, {mode})")
    t0 = time.time()
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    transcriber = BatchedInferencePipeline(model=model) if args.batched else model
    print(f"      model loaded in {time.time() - t0:.1f}s")

    print("[3/3] Transcribing...")
    t0 = time.time()
    kwargs = dict(language=args.language, vad_filter=True, beam_size=args.beam_size)
    if args.batched:
        kwargs["batch_size"] = args.batch_size
    segments_iter, tinfo = transcriber.transcribe(str(audio_path), **kwargs)
    print(f"      detected language: {tinfo.language} (prob {tinfo.language_probability:.2f})")

    segments = []
    for i, seg in enumerate(segments_iter, start=1):
        text = seg.text.strip()
        segments.append({"id": i, "start": seg.start, "end": seg.end, "text": text})
        print(f"  [{fmt_ts(seg.start)}] {text}")

    return {
        "segments": segments,
        "language": tinfo.language,
        "language_probability": tinfo.language_probability,
        "elapsed_sec": time.time() - t0,
    }


def transcribe_with_diarization(audio_path: Path, args, hf_token: str) -> dict:
    """Run WhisperX: transcribe -> align (word-level) -> diarize (speaker labels)."""
    import torch
    import whisperx

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    # whisperx prefers float16 / int8 on cuda; reuse user's compute_type but coerce common cases
    compute_type = args.compute_type
    if device == "cpu" and compute_type.startswith("int8_float"):
        compute_type = "int8"

    print(f"[2/4] Loading WhisperX model: {args.model} ({compute_type} on {device})")
    t0 = time.time()
    model = whisperx.load_model(args.model, device, compute_type=compute_type)
    print(f"      model loaded in {time.time() - t0:.1f}s")

    print("[3/4] Transcribing + word-level alignment...")
    t0 = time.time()
    audio = whisperx.load_audio(str(audio_path))
    result = model.transcribe(audio, batch_size=args.batch_size, language=args.language)
    detected_lang = result["language"]
    print(f"      detected language: {detected_lang}")

    align_model, align_meta = whisperx.load_align_model(language_code=detected_lang, device=device)
    result = whisperx.align(
        result["segments"], align_model, align_meta, audio, device,
        return_char_alignments=False,
    )
    transcribe_elapsed = time.time() - t0
    print(f"      transcribe + align: {transcribe_elapsed:.1f}s")

    print("[4/4] Diarizing speakers...")
    t0 = time.time()
    from whisperx.diarize import DiarizationPipeline
    # Pin to the 3.1 diarization model (pyannote 4 changed the default to "community-1",
    # which is a separate gated repo). 3.1 is the one users accepted in the setup steps.
    diarize_pipeline = DiarizationPipeline(
        model_name="pyannote/speaker-diarization-3.1",
        token=hf_token,
        device=device,
    )
    diarize_kwargs = {}
    if args.min_speakers is not None:
        diarize_kwargs["min_speakers"] = args.min_speakers
    if args.max_speakers is not None:
        diarize_kwargs["max_speakers"] = args.max_speakers
    diarize_segments = diarize_pipeline(audio, **diarize_kwargs)
    result = whisperx.assign_word_speakers(diarize_segments, result)
    diarize_elapsed = time.time() - t0
    print(f"      diarization: {diarize_elapsed:.1f}s")

    segments = []
    for i, seg in enumerate(result["segments"], start=1):
        speaker = seg.get("speaker", "SPEAKER_??")
        text = seg["text"].strip()
        segments.append({
            "id": i,
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "speaker": speaker,
            "text": text,
        })
        print(f"  [{fmt_ts(seg['start'])}] {speaker}: {text}")

    return {
        "segments": segments,
        "language": detected_lang,
        "language_probability": None,
        "elapsed_sec": transcribe_elapsed + diarize_elapsed,
        "diarized": True,
    }


def write_outputs(out_dir: Path, stem: str, info: dict, args, result: dict) -> None:
    diarized = result.get("diarized", False)

    text_parts = []
    srt_parts = []
    for seg in result["segments"]:
        speaker = seg.get("speaker")
        prefix = f"{speaker}: " if speaker else ""
        text_parts.append(f"{prefix}{seg['text']}")
        srt_parts.append(
            f"{seg['id']}\n{fmt_ts(seg['start'])} --> {fmt_ts(seg['end'])}\n{prefix}{seg['text']}\n"
        )

    (out_dir / f"{stem}.txt").write_text("\n".join(text_parts), encoding="utf-8")
    (out_dir / f"{stem}.srt").write_text("\n".join(srt_parts), encoding="utf-8")
    (out_dir / f"{stem}.json").write_text(
        json.dumps(
            {
                "url": args.url,
                "id": info.get("id"),
                "title": info.get("title") or info.get("id"),
                "duration_sec": info.get("duration"),
                "language": result["language"],
                "language_probability": result["language_probability"],
                "model": args.model,
                "compute_type": args.compute_type,
                "diarized": diarized,
                "segments": result["segments"],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Local YouTube transcription via faster-whisper / WhisperX.")
    ap.add_argument("url", help="YouTube video URL")
    ap.add_argument("--model", default="distil-large-v3",
                    help="Whisper model (default: distil-large-v3, English-only). "
                         "Use 'large-v3' for best multilingual / accent / proper-noun accuracy.")
    ap.add_argument("--compute-type", default="int8_float16",
                    help="CTranslate2 compute type (default: int8_float16)")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu", "auto"])
    ap.add_argument("--language", default=None,
                    help="Force language code (e.g. 'en'); auto-detect if omitted")
    ap.add_argument("--beam-size", type=int, default=5)
    ap.add_argument("--batched", action=argparse.BooleanOptionalAction, default=True,
                    help="Use BatchedInferencePipeline (default: on). --no-batched to disable.")
    ap.add_argument("--batch-size", type=int, default=16,
                    help="Batch size when --batched is set (default: 16)")
    ap.add_argument("--diarize", action="store_true",
                    help="Add speaker labels via WhisperX + pyannote. Requires HUGGINGFACE_TOKEN in .env.")
    ap.add_argument("--min-speakers", type=int, default=None,
                    help="Hint min number of speakers (diarize only)")
    ap.add_argument("--max-speakers", type=int, default=None,
                    help="Hint max number of speakers (diarize only)")
    ap.add_argument("--out", default="./output", help="Output directory")
    args = ap.parse_args()

    out_dir = Path(args.out).resolve()

    print(f"[1/{4 if args.diarize else 3}] Downloading audio: {args.url}")
    t0 = time.time()
    audio_path, info = download_audio(args.url, out_dir)
    duration = info.get("duration") or 0
    title = info.get("title") or info.get("id", "video")
    print(f"      -> {audio_path.name}  ({duration}s, {duration / 60:.1f} min)")
    print(f"      title: {title}")
    print(f"      download took {time.time() - t0:.1f}s")

    if args.diarize:
        hf_token = os.environ.get("HUGGINGFACE_TOKEN")
        if not hf_token:
            print("ERROR: --diarize requires HUGGINGFACE_TOKEN in .env (or env var). Aborting.",
                  file=sys.stderr)
            sys.exit(2)
        result = transcribe_with_diarization(audio_path, args, hf_token)
    else:
        result = transcribe_plain(audio_path, args)

    elapsed = result["elapsed_sec"]
    rt_factor = (duration / elapsed) if elapsed > 0 and duration else 0.0
    print(f"\n      processed in {elapsed:.1f}s ({rt_factor:.1f}x realtime)")

    write_outputs(out_dir, audio_path.stem, info, args, result)
    print(f"\nOutputs in: {out_dir}")
    print(f"  {audio_path.stem}.txt   (plain text)")
    print(f"  {audio_path.stem}.srt   (subtitles)")
    print(f"  {audio_path.stem}.json  (segments + metadata)")


if __name__ == "__main__":
    main()
