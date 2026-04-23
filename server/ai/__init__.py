"""Registry of analysis providers.

Providers are instantiated once at import time — they must not perform
any network / subprocess work in their constructors.
"""
from __future__ import annotations

from .claude_cli import ClaudeCLIProvider
from .codex_cli import CodexCLIProvider
from .events import AnalysisEvent
from .ollama import OllamaProvider
from .provider import AnalysisProvider


_REGISTRY: dict[str, AnalysisProvider] = {
    "claude_cli": ClaudeCLIProvider(),
    "codex_cli": CodexCLIProvider(),
    "ollama": OllamaProvider(),
}


def get_provider(name: str) -> AnalysisProvider | None:
    return _REGISTRY.get(name)


def list_providers() -> list[dict]:
    out: list[dict] = []
    for p in _REGISTRY.values():
        ok, reason = p.available()
        out.append({
            "name": p.name,
            "display_name": p.display_name,
            "available": ok,
            "reason": reason,
            "models": p.list_models() if ok else [],
        })
    return out


__all__ = [
    "AnalysisEvent",
    "AnalysisProvider",
    "ClaudeCLIProvider",
    "CodexCLIProvider",
    "OllamaProvider",
    "get_provider",
    "list_providers",
]
