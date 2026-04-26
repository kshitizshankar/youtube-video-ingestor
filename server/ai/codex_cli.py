"""Codex CLI provider.

Spawns `codex exec --json ...` inside the video folder, parses the JSONL
event stream, and translates each Codex event into a normalized
AnalysisEvent so the downstream pipeline (server/analyze.py) can treat
Codex identically to Claude.

Gated behind AI_ENABLE_CODEX=1 until the provider has been exercised on
real videos — a stray `codex` binary on PATH shouldn't auto-surface to
the UI.

Notes on the Codex event format (codex-cli 0.122.0, `codex exec --json`):

- `{"type": "thread.started", "thread_id": "..."}`
- `{"type": "turn.started"}`
- `{"type": "item.started",   "item": {"id": "...", "type": "command_execution", "command": "...", "status": "in_progress"}}`
- `{"type": "item.completed", "item": {"id": "...", "type": "command_execution", "command": "...", "aggregated_output": "...", "exit_code": N, "status": "completed"|"declined"}}`
- `{"type": "item.completed", "item": {"id": "...", "type": "agent_message", "text": "..."}}`
- `{"type": "turn.completed", "usage": {"input_tokens": N, "cached_input_tokens": N, "output_tokens": N}}`
- `{"type": "turn.failed",    "error": {"message": "..."}}`
- `{"type": "error",          "message": "..."}`

Codex emits zero or more `agent_message` items per turn; the FINAL one
is the canonical answer. We pass `--output-last-message` to have codex
write that final text to a file, which we then parse via `_extract_json`
so the analysis prompt's "Output ONLY a single JSON object" contract
works exactly as it does for Claude.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Event
from typing import Any, Iterator

import psutil

from .events import AnalysisEvent


# Known codex-CLI model slugs (from ~/.codex/models_cache.json on 0.122.0).
# The CLI also accepts arbitrary slugs via --model; this list drives the UI
# picker. `gpt-5-codex` from the earlier stub is no longer supported on
# ChatGPT-authenticated codex, so it is removed.
_MODELS = [
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.2",
]


# ---------------------------------------------------------------------------
# Subprocess tree-kill (cancel) — mirrors claude_cli._kill_tree
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


def _command_label(command: str) -> str:
    """Turn a raw shell/powershell command line into a short stage label.

    Codex wraps most commands in a powershell `-Command '...'` invocation,
    which makes the raw string noisy; peel that off when we can."""
    if not command:
        return "Running command"
    cmd = command.strip()
    # Peel off `"...\powershell.exe" -Command '...'` to get the inner script.
    low = cmd.lower()
    if "powershell.exe" in low and "-command" in low:
        idx = low.find("-command")
        rest = cmd[idx + len("-command"):].strip()
        # Strip a single wrapping quote if present.
        if rest.startswith(("'", '"')) and rest.endswith(("'", '"')):
            rest = rest[1:-1].strip()
        if rest:
            cmd = rest
    return f"Running: {_shorten(cmd)}"


def _extract_json(text: str) -> dict[str, Any] | None:
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last < 0 or last <= first:
        return None
    try:
        return json.loads(text[first : last + 1])
    except json.JSONDecodeError:
        return None


class CodexCLIProvider:
    name = "codex_cli"
    display_name = "Codex CLI"

    def available(self) -> tuple[bool, str | None]:
        if not shutil.which("codex"):
            return False, "`codex` binary not found on PATH"
        if (os.environ.get("AI_ENABLE_CODEX") or "").strip() != "1":
            return False, "set AI_ENABLE_CODEX=1 to enable (provider is experimental)"
        return True, None

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
        cli = shutil.which("codex")
        if not cli:
            yield {"type": "error", "error_message": "codex CLI not found"}
            return

        # --output-last-message gives us a clean copy of the final agent
        # message without having to pick it out of the stream. Use a temp
        # file outside the video folder so we don't pollute the user's
        # output directory.
        last_msg_fd, last_msg_path = tempfile.mkstemp(
            prefix="codex-last-", suffix=".txt"
        )
        os.close(last_msg_fd)

        # `-s read-only` + `-c approval_policy=never` keeps codex
        # non-interactive: it will run trusted read-only commands without
        # approval, and anything it can't run on its own is simply
        # declined (no stdin prompt, no hang).
        cmd = [
            cli,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--ephemeral",
            "--cd",
            str(video_folder),
            "-s",
            "read-only",
            "-c",
            "approval_policy=never",
            "--output-last-message",
            last_msg_path,
        ]
        # Strict JSON output: pass the analysis schema so codex routes via
        # OpenAI structured outputs. Without this, smaller codex models
        # (e.g. gpt-5.4-mini) return markdown prose and `_extract_json`
        # fails downstream. Schema lives alongside this file.
        schema_path = Path(__file__).parent / "analyze_schema.json"
        if schema_path.is_file():
            cmd.extend(["--output-schema", str(schema_path)])
        if model:
            cmd.extend(["--model", model])
        # Prompt must be the final positional arg.
        cmd.append(prompt)

        creationflags = 0
        preexec_fn = None
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            preexec_fn = os.setsid  # type: ignore[assignment]

        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(video_folder),
                stdin=subprocess.DEVNULL,  # codex blocks waiting on stdin otherwise
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
            try:
                os.unlink(last_msg_path)
            except OSError:
                pass
            yield {"type": "error", "error_message": f"failed to launch codex: {e}"}
            return

        yield {"type": "stage", "stage": "Launching Codex…"}

        usage_running = {"tokens_in": 0, "tokens_out": 0, "cached_tokens": 0}
        final_usage: dict[str, Any] = {}
        collected_messages: list[str] = []
        turn_error: str | None = None
        turn_completed = False

        assert proc.stdout is not None
        try:
            for line in iter(proc.stdout.readline, ""):
                if cancel_event.is_set():
                    _kill_tree(proc)
                    yield {"type": "error", "error_message": "cancelled"}
                    return
                line = line.strip()
                if not line or not line.startswith("{"):
                    # Codex interleaves some plain-text error lines from its
                    # tool router on stdout (e.g. "ERROR codex_core::tools::router ...").
                    # Ignore anything that isn't a JSON object.
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue

                et = evt.get("type")
                if et == "thread.started":
                    yield {"type": "stage", "stage": "Starting Codex session"}
                elif et == "turn.started":
                    yield {"type": "stage", "stage": "Thinking…"}
                elif et == "item.started":
                    item = evt.get("item") or {}
                    itype = item.get("type")
                    if itype == "command_execution":
                        yield {
                            "type": "stage",
                            "stage": _command_label(item.get("command") or ""),
                        }
                elif et == "item.completed":
                    item = evt.get("item") or {}
                    itype = item.get("type")
                    if itype == "agent_message":
                        text = (item.get("text") or "").strip()
                        if text:
                            collected_messages.append(text)
                            yield {"type": "stage", "stage": "Composing answer"}
                    elif itype == "command_execution":
                        status = item.get("status")
                        if status == "declined":
                            yield {
                                "type": "stage",
                                "stage": "Command blocked by sandbox policy",
                            }
                elif et == "turn.completed":
                    turn_completed = True
                    usage = evt.get("usage") or {}
                    if usage:
                        final_usage = {
                            "tokens_in": int(usage.get("input_tokens") or 0),
                            "tokens_out": int(usage.get("output_tokens") or 0),
                            "cached_tokens": int(
                                usage.get("cached_input_tokens") or 0
                            ),
                        }
                        usage_running.update(final_usage)
                        yield {
                            "type": "usage",
                            "tokens_in": usage_running["tokens_in"],
                            "tokens_out": usage_running["tokens_out"],
                            "cached_tokens": usage_running["cached_tokens"],
                            "cost_usd": 0.0,
                        }
                    # Codex may emit multiple turns in theory; in exec mode
                    # it emits exactly one, after which stdout closes.
                elif et == "turn.failed":
                    err = evt.get("error") or {}
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    turn_error = str(msg) if msg else "codex turn failed"
                elif et == "error":
                    msg = evt.get("message") or "codex reported an error"
                    turn_error = str(msg)
                # All other event types (stream.delta, etc.) are ignored —
                # they're useful for the interactive UI but add nothing we
                # can translate into the stage/usage/done envelope.

            # stdout closed.
            stderr = ""
            if proc.stderr:
                try:
                    stderr = proc.stderr.read() or ""
                except Exception:
                    pass
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                _kill_tree(proc, grace_sec=1.0)

            duration_ms = int((time.monotonic() - start) * 1000)

            if turn_error is not None:
                yield {
                    "type": "error",
                    "error_message": f"codex error: {_shorten(turn_error, 400)}",
                }
                return

            if not turn_completed:
                yield {
                    "type": "error",
                    "error_message": (
                        f"codex exited rc={proc.returncode} without a completed turn. "
                        f"{_shorten(stderr, 300)}"
                    ),
                }
                return

            # Pull the final text: prefer --output-last-message file, fall
            # back to the last streamed agent_message if the file is empty
            # (e.g. codex died before writing it).
            last_text = ""
            try:
                last_text = Path(last_msg_path).read_text(
                    encoding="utf-8", errors="replace"
                ).strip()
            except OSError:
                last_text = ""
            if not last_text and collected_messages:
                last_text = collected_messages[-1]

            parsed = _extract_json(last_text) if last_text else None
            if parsed is None:
                yield {
                    "type": "error",
                    "error_message": (
                        "could not parse JSON from codex response: "
                        f"{_shorten(last_text, 200)}"
                    ),
                }
                return

            yield {
                "type": "done",
                "result": parsed,
                "duration_ms": duration_ms,
                "tokens_in": usage_running["tokens_in"],
                "tokens_out": usage_running["tokens_out"],
                "cached_tokens": usage_running["cached_tokens"],
                "cost_usd": 0.0,  # codex exec does not emit a per-run cost.
            }
        finally:
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                _kill_tree(proc, grace_sec=1.0)
            try:
                os.unlink(last_msg_path)
            except OSError:
                pass
