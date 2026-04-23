"""Post-transcribe analysis: runs `claude -p` inside a video's folder to
extract a summary, key takeaways, chapters, and highlights.

Exposes a generator-style API (`stream_analyze_video`) that yields live
progress events parsed from claude's `--output-format=stream-json --verbose`
stream. The legacy `analyze_video` is a thin sync wrapper that drains the
generator and returns the final path.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


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
# Parsing helpers
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict[str, Any] | None:
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last < 0 or last <= first:
        return None
    try:
        return json.loads(text[first : last + 1])
    except json.JSONDecodeError:
        return None


def _shorten(s: str, n: int = 60) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _tool_use_label(block: dict[str, Any]) -> str:
    name = block.get("name", "")
    inp = block.get("input") or {}
    if name == "Read":
        fp = inp.get("file_path") or inp.get("filePath") or ""
        return f"Reading {Path(fp).name}" if fp else "Reading file"
    if name == "Write":
        fp = inp.get("file_path") or ""
        return f"Writing {Path(fp).name}" if fp else "Writing file"
    if name == "Edit":
        fp = inp.get("file_path") or ""
        return f"Editing {Path(fp).name}" if fp else "Editing file"
    if name == "Bash":
        cmd = inp.get("command") or ""
        return f"Running: {_shorten(cmd)}" if cmd else "Running command"
    if name == "Grep":
        p = inp.get("pattern") or ""
        return f"Searching: {_shorten(p, 40)}"
    if name == "Glob":
        p = inp.get("pattern") or ""
        return f"Finding: {_shorten(p, 40)}"
    if name == "TodoWrite":
        return "Planning steps"
    return f"Using {name}"


def _event_label(evt: dict[str, Any]) -> str | None:
    t = evt.get("type")
    if t == "system" and evt.get("subtype") == "init":
        return "Starting Claude session"
    if t == "assistant":
        msg = evt.get("message") or {}
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return _tool_use_label(block)
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text = (block.get("text") or "").strip()
                if text:
                    # Don't leak internal chain-of-thought; just acknowledge.
                    return "Composing answer"
        return None
    if t == "user":
        # Tool result returning to the model — noisy, skip.
        return None
    if t == "result":
        if evt.get("is_error"):
            return "Claude returned an error"
        return "Finalizing"
    return None


# ---------------------------------------------------------------------------
# Streaming runner
# ---------------------------------------------------------------------------


def _accumulate_usage(tot: dict[str, int], usage: dict[str, Any]) -> None:
    for k in ("input_tokens", "output_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens"):
        v = usage.get(k)
        if isinstance(v, (int, float)):
            tot[k] = tot.get(k, 0) + int(v)


def stream_analyze_video(out_dir: Path, video_id: str) -> Iterator[dict[str, Any]]:
    """Run claude with stream-json output and yield progress events.

    Emitted event shapes:
      progress: { type, message, raw_type, tokens_in, tokens_out, cache_read, cost_usd }
      done:     { type, path, duration_ms, duration_api_ms, tokens_in, tokens_out,
                  cache_read, cache_creation, cost_usd, num_turns }
      error:    { type, message }
    """
    video_folder = out_dir / video_id
    if not (video_folder / "transcript.json").exists():
        yield {"type": "error", "message": f"no transcript.json in {video_folder}"}
        return

    claude = shutil.which("claude")
    if not claude:
        yield {"type": "error", "message": "claude CLI not found in PATH"}
        return

    cmd = [
        claude,
        "-p", ANALYZE_PROMPT,
        "--output-format=stream-json",
        "--verbose",
    ]

    usage_running: dict[str, int] = {}
    cost_running: float = 0.0

    def _with_usage(e: dict[str, Any]) -> dict[str, Any]:
        e.setdefault("tokens_in", usage_running.get("input_tokens", 0))
        e.setdefault("tokens_out", usage_running.get("output_tokens", 0))
        e.setdefault("cache_read", usage_running.get("cache_read_input_tokens", 0))
        e.setdefault("cache_creation", usage_running.get("cache_creation_input_tokens", 0))
        e.setdefault("cost_usd", cost_running)
        return e

    yield _with_usage({"type": "progress", "message": "Launching Claude…", "raw_type": "launch"})

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(video_folder),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # line-buffered
        )
    except OSError as e:
        yield {"type": "error", "message": f"failed to launch claude: {e}"}
        return

    final_result_text: str | None = None
    duration_ms: int | None = None
    duration_api_ms: int | None = None
    num_turns: int | None = None

    assert proc.stdout is not None
    for line in iter(proc.stdout.readline, ""):
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Accumulate per-turn usage from assistant messages.
        if evt.get("type") == "assistant":
            msg = evt.get("message") or {}
            u = msg.get("usage") or {}
            _accumulate_usage(usage_running, u)

        label = _event_label(evt)
        if label:
            yield _with_usage({
                "type": "progress",
                "message": label,
                "raw_type": evt.get("type"),
            })

        if evt.get("type") == "result":
            if evt.get("is_error"):
                yield {
                    "type": "error",
                    "message": f"claude error: {_shorten(str(evt.get('result') or evt), 400)}",
                }
                try: proc.wait(timeout=5)
                except Exception: pass
                return
            final_result_text = evt.get("result") or ""
            duration_ms = evt.get("duration_ms")
            duration_api_ms = evt.get("duration_api_ms")
            num_turns = evt.get("num_turns")
            cost_running = float(evt.get("total_cost_usd") or 0.0)
            # Final usage (if present) replaces the running accumulator.
            final_usage = evt.get("usage") or {}
            if final_usage:
                usage_running = {k: int(final_usage.get(k, 0)) for k in (
                    "input_tokens", "output_tokens",
                    "cache_creation_input_tokens", "cache_read_input_tokens",
                ) if final_usage.get(k) is not None}

    proc.wait()

    if final_result_text is None:
        stderr = proc.stderr.read() if proc.stderr else ""
        yield {
            "type": "error",
            "message": f"claude exited without a result (rc={proc.returncode}). {_shorten(stderr, 400)}",
        }
        return

    yield _with_usage({"type": "progress", "message": "Parsing JSON", "raw_type": "parse"})
    parsed = _extract_json(final_result_text)
    if parsed is None:
        yield {
            "type": "error",
            "message": "failed to parse JSON from claude response",
        }
        return

    # Embed generation metadata in the saved file so later reads can show
    # cost / duration without needing to run claude again.
    parsed["_meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": duration_ms,
        "duration_api_ms": duration_api_ms,
        "num_turns": num_turns,
        "cost_usd": cost_running,
        "tokens_in": usage_running.get("input_tokens", 0),
        "tokens_out": usage_running.get("output_tokens", 0),
        "cache_read_tokens": usage_running.get("cache_read_input_tokens", 0),
        "cache_creation_tokens": usage_running.get("cache_creation_input_tokens", 0),
    }

    out_path = video_folder / "analysis.json"
    out_path.write_text(
        json.dumps(parsed, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    yield {
        "type": "done",
        "path": out_path.name,
        "duration_ms": duration_ms,
        "duration_api_ms": duration_api_ms,
        "num_turns": num_turns,
        "tokens_in": usage_running.get("input_tokens", 0),
        "tokens_out": usage_running.get("output_tokens", 0),
        "cache_read": usage_running.get("cache_read_input_tokens", 0),
        "cache_creation": usage_running.get("cache_creation_input_tokens", 0),
        "cost_usd": cost_running,
    }


# ---------------------------------------------------------------------------
# Sync wrapper (backward-compat)
# ---------------------------------------------------------------------------


def analyze_video(out_dir: Path, video_id: str) -> Path | None:
    """Drain the stream; return the analysis.json path on success, else None."""
    for evt in stream_analyze_video(out_dir, video_id):
        if evt.get("type") == "done":
            return out_dir / video_id / (evt.get("path") or "analysis.json")
        if evt.get("type") == "error":
            log.warning("analyze_video failed: %s", evt.get("message"))
            return None
    return None
