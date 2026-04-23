"""Registry sanity: three providers present, unavailable ones carry a reason."""
from __future__ import annotations

from server.ai import get_provider, list_providers


def test_registry_has_the_three():
    names = {p["name"] for p in list_providers()}
    assert names == {"claude_cli", "codex_cli", "ollama"}


def test_unavailable_shown_as_such():
    # Each provider reports a (bool, reason) shape. Unavailable ones must
    # supply a non-empty reason so the UI can render "why not".
    for p in list_providers():
        if not p["available"]:
            assert p.get("reason"), f"{p['name']} lacks a reason when unavailable"


def test_get_provider_unknown_returns_none():
    assert get_provider("no-such-provider") is None


def test_get_provider_known_returns_instance():
    cc = get_provider("claude_cli")
    assert cc is not None
    assert cc.name == "claude_cli"
    assert cc.display_name == "Claude Code CLI"
