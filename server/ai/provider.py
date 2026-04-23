"""Protocol definition for pluggable analysis providers.

Each provider wraps an AI backend (CLI or HTTP) and emits normalized
AnalysisEvent values. Providers must be cheap to instantiate at import
time — any network / subprocess work belongs inside `available()` or
`stream_analyze()`, not `__init__`.
"""
from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Iterator, Protocol

from .events import AnalysisEvent


class AnalysisProvider(Protocol):
    name: str            # machine id: "claude_cli", "codex_cli", "ollama"
    display_name: str    # human label

    def available(self) -> tuple[bool, str | None]:
        """(True, None) if ready to use; (False, reason) if not."""
        ...

    def list_models(self) -> list[str]:
        """Model ids this provider exposes. Empty list = provider default."""
        ...

    def stream_analyze(
        self,
        *,
        video_folder: Path,
        prompt: str,
        model: str | None,
        cancel_event: Event,
    ) -> Iterator[AnalysisEvent]:
        """Run the analysis, yielding normalized events. Must terminate
        promptly on cancel_event.set() (within 2s for subprocess providers)."""
        ...
