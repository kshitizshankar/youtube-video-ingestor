"""Post-transcribe analysis: runs `claude -p` inside a video's folder to
extract a summary, key takeaways, chapters, and highlights. Output lands
at `output/<video_id>/analysis.json`.

Uses the Claude Code CLI (authenticated with the user's existing account)
so we don't need a separate API key. Any user who runs this will see usage
count against their Claude Code subscription.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any


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


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the outermost { ... } block and parse it. Tolerant of any pre/post
    whitespace, code fences, or chatter from the CLI."""
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last < 0 or last <= first:
        return None
    try:
        return json.loads(text[first : last + 1])
    except json.JSONDecodeError as e:
        log.warning("failed to parse analysis JSON: %s", e)
        return None


def run_claude_analyze(video_dir: Path, timeout_sec: int = 900) -> dict[str, Any] | None:
    """Run `claude -p <prompt>` inside `video_dir` and parse its JSON reply.
    Returns None on any failure — callers should treat analysis as optional."""
    claude = shutil.which("claude")
    if not claude:
        log.error("claude CLI not found in PATH; skipping analysis")
        return None

    try:
        proc = subprocess.run(
            [claude, "-p", ANALYZE_PROMPT],
            cwd=str(video_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.error("claude -p timed out after %ss in %s", timeout_sec, video_dir)
        return None
    except OSError as e:
        log.error("claude -p failed to launch: %s", e)
        return None

    if proc.returncode != 0:
        log.error(
            "claude -p non-zero exit %s in %s; stderr head=%s",
            proc.returncode, video_dir, (proc.stderr or "")[:500],
        )
        return None

    return _extract_json(proc.stdout or "")


def analyze_video(out_dir: Path, video_id: str) -> Path | None:
    """Generate analysis.json for `output/<video_id>/`. Idempotent — running
    it again overwrites the existing analysis."""
    video_folder = out_dir / video_id
    if not (video_folder / "transcript.json").exists():
        return None
    result = run_claude_analyze(video_folder)
    if result is None:
        return None
    out = video_folder / "analysis.json"
    out.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out
