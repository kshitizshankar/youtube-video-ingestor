# Notes

## Parked: NVIDIA Parakeet (potential future swap from faster-whisper)

Parakeet is NVIDIA's ASR model family, shipped via the NeMo toolkit and on Hugging Face.

**Headline numbers**
- Flagship: `parakeet-tdt-0.6b-v2` (~600M params).
- ~3,000× realtime on A100; realistic ~100–300× on RTX 4060.
- Topped the HuggingFace Open ASR Leaderboard at release (~mid-2024), matching/beating Whisper large-v3 on English benchmarks at 1/3 the params.
- License: CC-BY-4.0 (commercial OK).

**Why it's fast (architectural)**
- Whisper: encoder-decoder transformer, autoregressive over 30s windows, token-by-token.
- Parakeet: FastConformer encoder + **TDT (Token-and-Duration Transducer)** decoder. TDT predicts the next token *and* how long it lasts, so it can skip frames during silence/predictable audio rather than running the decoder on every frame. That's the 10–100× speedup vs. Whisper.

**Cost of switching**
- **English-only** for the well-tested variants (newer multilingual versions exist, less battle-tested).
- **NeMo is heavy** — pulls PyTorch Lightning, Hydra, etc. Install is several GB and version-conflict-prone on Windows. Most users run it in WSL2 or a container.
- No first-class Windows wheels historically.

**When to revisit**
- Daily/scaled English transcription where current Whisper speed becomes a bottleneck.
- If we ever want sub-minute turnaround on multi-hour videos and are willing to take on the NeMo install.

**For now (2026-04-20)**
- Sticking with faster-whisper. Optimizing via batched inference + `distil-large-v3` model gets us to ~5 min for a 2hr video, which is fine for the current use case.

## Parked: Audio preprocessing (denoise / vocal isolation)

Whisper is reasonably noise-robust on its own, and the VAD filter we already use (`vad_filter=True`) strips silence. So most talking-head YouTube videos transcribe fine without any preprocessing.

**When preprocessing would actually help**
- Music videos / podcasts with intro music or background score (vocals get garbled).
- Heavy reverb (cathedrals, large halls).
- Phone-recorded or outdoor audio with wind/traffic.
- Multiple overlapping speakers.

**Options to consider when needed**

| Tool | Job | Cost |
|---|---|---|
| **DeepFilterNet** | Lightweight neural noise suppression. Good first thing to try. | Small (~10MB), fast (~30× realtime) |
| **Demucs** (Meta) | Source separation — splits vocals from music. Best for music-heavy audio. | Heavy (~1GB model, ~5–10× realtime) |
| **ffmpeg highpass/lowpass** | Strip hum/hiss with classic DSP filters. | Free, near-instant |
| **Resemble Enhance** | Speech enhancement + denoising, very high quality. | Heavy |
| **VoiceFixer** | Speech restoration including reverb removal. | Heavy |

**Suggested wiring when we get to it**
- Add an optional `--denoise` flag → runs DeepFilterNet before transcription. Fast enough to leave on.
- Add an optional `--isolate-vocals` flag → runs Demucs first. Slow, only useful when we know the source has music.
- Don't denoise by default — it adds latency for content that doesn't benefit.
