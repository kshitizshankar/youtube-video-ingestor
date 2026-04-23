"""Codex CLI provider — stub.

Gated behind AI_ENABLE_CODEX=1 so a stray `codex` binary on PATH doesn't
auto-surface before we've tested the event translation.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from threading import Event
from typing import Iterator

from .events import AnalysisEvent


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
        return ["gpt-5-codex"]

    def stream_analyze(
        self,
        *,
        video_folder: Path,
        prompt: str,
        model: str | None,
        cancel_event: Event,
    ) -> Iterator[AnalysisEvent]:
        raise NotImplementedError(
            "codex_cli provider is stubbed — wire event translation once "
            "the binary is available for testing"
        )
