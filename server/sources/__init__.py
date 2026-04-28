"""Source providers package.

Public surface:

    from server.sources import (
        Provider, Source, SourceList, ProviderError,
        dispatch, all_providers,
    )

Each provider knows how to recognise, resolve, and download a class of
URLs. The processing pipeline (queue -> whisperx -> diarize -> analyze)
consumes Sources directly and is provider-agnostic.
"""
from __future__ import annotations

from .audio import GenericAudioProvider
from .base import Provider, ProviderError, Source, SourceKind, SourceList, validate_url
from .registry import all_providers, dispatch
from .rss import RSSProvider
from .spotify import SpotifyProvider
from .youtube import YouTubeProvider


__all__ = [
    "Provider",
    "ProviderError",
    "Source",
    "SourceKind",
    "SourceList",
    "validate_url",
    "all_providers",
    "dispatch",
    "GenericAudioProvider",
    "RSSProvider",
    "SpotifyProvider",
    "YouTubeProvider",
]
