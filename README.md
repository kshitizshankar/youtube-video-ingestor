# youtube-video-ingestor

Local YouTube transcription pipeline. Pulls a video's audio with `yt-dlp`,
transcribes it with `faster-whisper` (optionally via `WhisperX` for speaker
diarization), and writes plain text, SRT subtitles, and JSON segments
side-by-side.

Built around an 8 GB VRAM consumer GPU (RTX 4060). All speeds below are
measured on that card.

## Speed

| Pipeline | Realtime | 2 hr video |
|---|---|---|
| `large-v3` (sequential, accuracy baseline) | ~5× | ~25 min |
| `large-v3` + batched | ~22× | ~5.5 min |
| `distil-large-v3` + batched (default) | ~50–70× | ~2–3 min |
| `--diarize` (transcribe + word-align + speakers) | ~12–14× | ~10 min |

`distil-large-v3` is English-only. Switch to `--model large-v3` for
multilingual / accented / proper-noun-heavy content.

## Setup

```bash
uv sync                               # installs deps + pinned PyTorch CUDA wheels
cp .env.example .env                  # paste a HuggingFace token (only needed for --diarize)
```

For `--diarize` you must also accept the user agreements on:

- https://huggingface.co/pyannote/segmentation-3.0
- https://huggingface.co/pyannote/speaker-diarization-3.1
- https://huggingface.co/pyannote/speaker-diarization-community-1

## Use

Single video:

```bash
uv run python ingest.py "https://www.youtube.com/watch?v=…"
uv run python ingest.py "<url>" --diarize             # add speaker labels
uv run python ingest.py "<url>" --model large-v3      # quality model
```

Batch from a markdown file (extracts every YouTube link, dedupes, skips
videos whose `<id>.json` already exists in `output/`):

```bash
uv run python batch.py --from-markdown path/to/some.md
uv run python batch.py --urls urls.txt --diarize
uv run python batch.py "<url1>" "<url2>" "<url3>"
```

## Outputs

In `./output/<video_id>.{txt,srt,json,mp3}`:

- `.txt` — plain transcript (with `SPEAKER_xx:` prefixes when diarized)
- `.srt` — subtitles with timestamps
- `.json` — segments + metadata (model, language, duration, speaker labels)
- `.mp3` — original audio (kept for re-runs)

## Notes

See [`NOTES.md`](./NOTES.md) for parked future work — NVIDIA Parakeet (much
faster ASR, English-only, NeMo install pain) and audio preprocessing
(DeepFilterNet for denoise, Demucs for vocal isolation).
