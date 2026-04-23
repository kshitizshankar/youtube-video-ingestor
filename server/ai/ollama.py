"""Ollama (local) provider — stub.

Availability probe hits localhost:11434/api/tags lazily; no network calls
happen at import time.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from typing import Any, Iterator
from urllib.error import URLError
from urllib.request import Request, urlopen

from .events import AnalysisEvent


_TAGS_URL = "http://localhost:11434/api/tags"


class OllamaProvider:
    name = "ollama"
    display_name = "Ollama (local)"

    def _tags(self) -> dict[str, Any] | None:
        try:
            with urlopen(Request(_TAGS_URL), timeout=0.5) as r:
                return json.loads(r.read().decode("utf-8"))
        except (URLError, OSError, json.JSONDecodeError, TimeoutError):
            return None

    def available(self) -> tuple[bool, str | None]:
        if self._tags() is None:
            return False, "Ollama daemon not reachable at localhost:11434"
        return True, None

    def list_models(self) -> list[str]:
        t = self._tags()
        if not t:
            return []
        return [m["name"] for m in (t.get("models") or []) if "name" in m]

    def stream_analyze(
        self,
        *,
        video_folder: Path,
        prompt: str,
        model: str | None,
        cancel_event: Event,
    ) -> Iterator[AnalysisEvent]:
        raise NotImplementedError(
            "ollama provider is stubbed — /api/generate streaming translator pending"
        )
