"""Provider registry + dispatch helper.

Order matters: we ask each provider's `can_handle()` in turn until one
claims the URL. Cheap providers (regex-only `can_handle`) come before
those that may fire a HEAD probe.

Order:
  1. YouTubeProvider     — host-match on youtube.com / youtu.be / *.youtube.com
  2. SpotifyProvider     — host+path-regex on open.spotify.com/show|episode
  3. RSSProvider         — .rss / .xml suffix, then HEAD content-type
  4. GenericAudioProvider — .mp3 / .m4a / etc. suffix, then HEAD audio/*
"""
from __future__ import annotations

from .audio import GenericAudioProvider
from .base import Provider, ProviderError
from .rss import RSSProvider
from .spotify import SpotifyProvider
from .youtube import YouTubeProvider


_PROVIDERS: list[Provider] = [
    YouTubeProvider(),
    SpotifyProvider(),
    RSSProvider(),
    GenericAudioProvider(),
]


def all_providers() -> list[Provider]:
    """Return the live list of providers in dispatch order. Mostly useful
    for tests and admin tooling."""
    return list(_PROVIDERS)


def dispatch(url: str) -> Provider:
    """Return the first provider whose `can_handle(url)` is True. Raises
    ProviderError if no provider accepts the URL."""
    for p in _PROVIDERS:
        try:
            if p.can_handle(url):
                return p
        except Exception:
            # A provider's can_handle should never raise -- but if it does
            # (e.g. a transient network failure on a HEAD probe), skip it
            # rather than failing the entire dispatch chain.
            continue
    raise ProviderError(f"no provider can handle URL: {url}")
