"""RSS provider — direct .rss / .xml feed URLs (and content-type probe).
The download path is identical to Spotify's: yt-dlp generic extractor
on the publisher CDN's mp3 URL.
"""
from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from .base import Provider, ProviderError, Source, SourceList, validate_url
from .spotify import _preview_to_sourcelist


_RSS_PATH_RE = re.compile(r"\.(rss|xml|atom)(?:$|\?)", re.IGNORECASE)


class RSSProvider(Provider):
    name = "rss"

    def can_handle(self, url: str) -> bool:
        try:
            parsed = urllib.parse.urlsplit(url)
        except Exception:
            return False
        if parsed.scheme not in ("http", "https"):
            return False
        # 1) Suffix probe is the cheap path -- catches all the common cases:
        #    rss.buzzsprout.com/.../feed.rss, feeds.megaphone.fm/.../foo.xml.
        if _RSS_PATH_RE.search(parsed.path or ""):
            return True
        # 2) Content-type HEAD probe -- only when the suffix didn't match
        #    AND nothing earlier in the chain claimed the URL. Some podcast
        #    hosts return a feed at a clean path with the right MIME.
        try:
            from .. import podcast as _podcast
            ct = _podcast._http_head_content_type(url)
        except Exception:
            return False
        if not ct:
            return False
        return "rss" in ct or "atom" in ct or "xml" in ct

    # ------------------------------------------------------------------

    def resolve(self, url: str) -> SourceList:
        validate_url(url)
        from .. import podcast as podcast_mod
        try:
            preview = podcast_mod.preview_podcast(url)
        except podcast_mod.PodcastError as e:
            raise ProviderError(str(e)) from e
        return _preview_to_sourcelist(preview)

    # ------------------------------------------------------------------

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
