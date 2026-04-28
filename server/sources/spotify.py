"""Spotify provider — open.spotify.com/show/* and /episode/* URLs.

Spotify itself doesn't expose episode audio, so we (a) scrape the embed
page for show name + publisher, (b) resolve to the iTunes feedUrl, (c)
parse the resulting RSS. Audio download then uses yt-dlp's generic
extractor on the publisher CDN's mp3 URL.

The heavy lifting (Spotify scraping, iTunes resolution, RSS parsing,
SSRF gate, redirect-validation) lives in `server.podcast`; this provider
just adapts the dataclasses.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .base import Provider, ProviderError, Source, SourceList, validate_url


_SPOTIFY_SHOW_RE = re.compile(
    r"^https?://open\.spotify\.com/(?:[a-z-]+/)?show/([A-Za-z0-9]+)"
)
_SPOTIFY_EPISODE_RE = re.compile(
    r"^https?://open\.spotify\.com/(?:[a-z-]+/)?episode/([A-Za-z0-9]+)"
)


class SpotifyProvider(Provider):
    name = "spotify"

    def can_handle(self, url: str) -> bool:
        u = (url or "").strip()
        return bool(
            _SPOTIFY_SHOW_RE.match(u) or _SPOTIFY_EPISODE_RE.match(u)
        )

    def resolve(self, url: str) -> SourceList:
        # Reuse the existing public entry point so all the iTunes
        # disambiguation, embed scraping, and redirect-validation logic
        # stays in one place.
        from .. import podcast as podcast_mod
        try:
            preview = podcast_mod.preview_podcast(url)
        except podcast_mod.PodcastError as e:
            raise ProviderError(str(e)) from e

        return _preview_to_sourcelist(preview)

    def download(
        self,
        source: Source,
        out_dir: Path,
        *,
        progress_hook: Callable[[dict], None] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        validate_url(source.url)
        from ..transcriber import download_audio
        return download_audio(source.url, out_dir, progress_hook=progress_hook)


# ---------------------------------------------------------------------------
# Shared adapter -- used by SpotifyProvider AND RSSProvider since both
# resolve into the same `PodcastPreview` shape internally.
# ---------------------------------------------------------------------------


def _preview_to_sourcelist(preview: Any) -> SourceList:
    """Translate a `podcast.PodcastPreview` into our generic SourceList."""
    from ..transcriber import _safe_video_id

    sources: list[Source] = []
    for ep in preview.episodes:
        sources.append(Source(
            kind="podcast",
            url=ep.mp3_url,
            title=ep.title or "(untitled)",
            description=ep.description,
            duration_sec=ep.duration_sec,
            pub_date=ep.pub_date,
            image_url=ep.image_url or preview.image_url,
            host=preview.publisher,
            show_name=preview.title,
            show_url=preview.rss_url,
            safe_id=_safe_video_id(ep.mp3_url),
            extra={"guid": ep.guid},
        ))
    return SourceList(
        title=preview.title,
        publisher=preview.publisher,
        description=preview.description,
        image_url=preview.image_url,
        rss_url=preview.rss_url,
        sources=sources,
    )
