"""Claude Code CLI provider.

Spawns `claude -p <prompt> --output-format=stream-json --verbose` inside
the video folder, parses the stream, and translates each Claude event
into a normalized AnalysisEvent.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path
from threading import Event
from typing import Any, Iterator

import psutil

from .events import AnalysisEvent


_MODELS = [
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]


# ---------------------------------------------------------------------------
# Subprocess tree-kill (cancel)
# ---------------------------------------------------------------------------


def _kill_tree(proc: subprocess.Popen, grace_sec: float = 2.0) -> None:
    """Kill the whole subprocess tree. Windows uses CTRL_BREAK_EVENT then
    psutil-based force kill; POSIX uses os.killpg for the process group."""
    try:
        parent = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        return
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []

    if os.name == "nt":
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            pass

    try:
        proc.wait(timeout=grace_sec)
    except subprocess.TimeoutExpired:
        pass

    # Force kill anything still alive.
    for c in children + [parent]:
        try:
            if c.is_running():
                c.kill()
        except psutil.NoSuchProcess:
            pass


# ---------------------------------------------------------------------------
# Event translation helpers
# ---------------------------------------------------------------------------


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
        return f"Searching: {_shorten(inp.get('pattern') or '', 40)}"
    if name == "Glob":
        return f"Finding: {_shorten(inp.get('pattern') or '', 40)}"
    if name == "TodoWrite":
        return "Planning steps"
    return f"Using {name}" if name else "Using tool"


def _extract_json(text: str) -> dict[str, Any] | None:
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last < 0 or last <= first:
        return None
    try:
        return json.loads(text[first : last + 1])
    except json.JSONDecodeError:
        return None


class ClaudeCLIProvider:
    name = "claude_cli"
    display_name = "Claude Code CLI"

    def available(self) -> tuple[bool, str | None]:
        if shutil.which("claude"):
            return True, None
        return False, "`claude` binary not found on PATH"

    def list_models(self) -> list[str]:
        return list(_MODELS)

    def stream_analyze(
        self,
        *,
        video_folder: Path,
        prompt: str,
        model: str | None,
        cancel_event: Event,
    ) -> Iterator[AnalysisEvent]:
        cli = shutil.which("claude")
        if not cli:
            yield {"type": "error", "error_message": "claude CLI not found"}
            return
        cmd = [cli, "-p", prompt, "--output-format=stream-json", "--verbose"]
        if model:
            cmd.extend(["--model", model])

        creationflags = 0
        preexec_fn = None
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            preexec_fn = os.setsid  # type: ignore[assignment]

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(video_folder),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                preexec_fn=preexec_fn,
            )
        except OSError as e:
            yield {"type": "error", "error_message": f"failed to launch claude: {e}"}
            return

        yield {"type": "stage", "stage": "Launching Claude…"}

        usage_running = {"tokens_in": 0, "tokens_out": 0, "cached_tokens": 0}
        cost_running = 0.0

        assert proc.stdout is not None
        try:
            for line in iter(proc.stdout.readline, ""):
                if cancel_event.is_set():
                    _kill_tree(proc)
                    yield {"type": "error", "error_message": "cancelled"}
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue

                et = evt.get("type")
                if et == "system" and evt.get("subtype") == "init":
                    yield {"type": "stage", "stage": "Starting Claude session"}
                elif et == "assistant":
                    msg = evt.get("message") or {}
                    u = msg.get("usage") or {}
                    changed = False
                    for src, dst in (
                        ("input_tokens", "tokens_in"),
                        ("output_tokens", "tokens_out"),
                        ("cache_read_input_tokens", "cached_tokens"),
                    ):
                        v = u.get(src)
                        if isinstance(v, (int, float)):
                            usage_running[dst] += int(v)
                            changed = True
                    if changed:
                        yield {
                            "type": "usage",
                            "tokens_in": usage_running["tokens_in"],
                            "tokens_out": usage_running["tokens_out"],
                            "cached_tokens": usage_running["cached_tokens"],
                            "cost_usd": cost_running,
                        }
                    # Stage label — tool_use wins, else text block -> Composing.
                    label: str | None = None
                    for block in msg.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            label = _tool_use_label(block)
                            break
                    if label is None:
                        for block in msg.get("content") or []:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = (block.get("text") or "").strip()
                                if text:
                                    label = "Composing answer"
                                    break
                    if label:
                        yield {"type": "stage", "stage": label}
                elif et == "result":
                    if evt.get("is_error"):
                        yield {
                            "type": "error",
                            "error_message": (
                                f"claude error: "
                                f"{_shorten(str(evt.get('result') or evt), 400)}"
                            ),
                        }
                        return
                    cost_running = float(evt.get("total_cost_usd") or 0.0)
                    duration_ms = int(evt.get("duration_ms") or 0)
                    parsed = _extract_json(evt.get("result") or "")
                    if parsed is None:
                        yield {
                            "type": "error",
                            "error_message": "could not parse JSON from claude response",
                        }
                        return
                    # Final usage replaces running accumulator if present.
                    final_usage = evt.get("usage") or {}
                    if final_usage:
                        usage_running["tokens_in"] = int(
                            final_usage.get("input_tokens", usage_running["tokens_in"])
                        )
                        usage_running["tokens_out"] = int(
                            final_usage.get("output_tokens", usage_running["tokens_out"])
                        )
                        usage_running["cached_tokens"] = int(
                            final_usage.get(
                                "cache_read_input_tokens",
                                usage_running["cached_tokens"],
                            )
                        )
                    yield {
                        "type": "done",
                        "result": parsed,
                        "duration_ms": duration_ms,
                        "tokens_in": usage_running["tokens_in"],
                        "tokens_out": usage_running["tokens_out"],
                        "cached_tokens": usage_running["cached_tokens"],
                        "cost_usd": cost_running,
                    }
                    return
            # stdout closed without a result
            stderr = ""
            if proc.stderr:
                try:
                    stderr = proc.stderr.read() or ""
                except Exception:
                    pass
            yield {
                "type": "error",
                "error_message": (
                    f"claude exited rc={proc.returncode} without a result. "
                    f"{_shorten(stderr, 300)}"
                ),
            }
        finally:
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                _kill_tree(proc, grace_sec=1.0)
