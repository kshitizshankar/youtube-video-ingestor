"""Generic audio provider — direct .mp3 / .m4a / .wav / .ogg / .opus URLs,
and a HEAD-probe catch-all for anything serving `audio/*`. The download
path uses yt-dlp's generic extractor (same opts as `transcriber.download_audio`)
so we get the FFmpegExtractAudio post-process and the safe-folder scheme
for free.
"""
from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from .base import Provider, ProviderError, Source, SourceList, validate_url


_AUDIO_EXT_RE = re.compile(
    r"\.(mp3|m4a|wav|ogg|aac|opus|flac|webm)(?:$|\?)", re.IGNORECASE
)


class GenericAudioProvider(Provider):
    name = "audio"

    def can_handle(self, url: str) -> bool:
        try:
            parsed = urllib.parse.urlsplit(url)
        except Exception:
            return False
        if parsed.scheme not in ("http", "https"):
            return False
        # 1) Path-suffix probe is cheap and covers ~all podcast CDNs.
        if _AUDIO_EXT_RE.search(parsed.path or ""):
            return True
        # 2) Optional HEAD probe -- only fire when nothing else claimed the
        # URL. We keep this off the hot path for now: the registry's catch-
        # all ordering means GenericAudioProvider is asked LAST, so a true
        # `audio/*` URL with no extension will only land here if neither
        # YouTube nor RSS nor Spotify took it. The HEAD probe is a server-
        # side fetch -- we run it through the SSRF gate before firing.
        try:
            from .. import podcast as _podcast
            ct = _podcast._http_head_content_type(url)
        except Exception:
            return False
        return bool(ct and ct.startswith("audio/"))

    # ------------------------------------------------------------------

    def resolve(self, url: str) -> SourceList:
        validate_url(url)
        # Local import to avoid module-load cycles.
        from ..transcriber import _safe_video_id

        path = urllib.parse.urlsplit(url).path
        filename = path.rsplit("/", 1)[-1] or url
        title = re.sub(r"\.(mp3|m4a|aac|wav|ogg|opus|flac|webm)$", "",
                       filename, flags=re.IGNORECASE)
        if not title:
            title = filename or "(untitled audio)"

        src = Source(
            kind="audio",
            url=url,
            title=title,
            description=None,
            duration_sec=None,
            pub_date=None,
            image_url=None,
            host=None,
            show_name=None,
            show_url=None,
            safe_id=_safe_video_id(url),
            extra={},
        )
        return SourceList(
            title=title,
            publisher=None,
            description=None,
            image_url=None,
            rss_url=None,
            sources=[src],
        )

    # ------------------------------------------------------------------

    def download(
        self,
        source: Source,
        out_dir: Path,
        *,
        progress_hook: Callable[[dict], None] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        # SSRF gate before any network IO, even though the existing
        # download_audio path eventually hits yt-dlp -- yt-dlp won't refuse
        # an internal IP on its own.
        validate_url(source.url)
        from ..transcriber import download_audio
        return download_audio(source.url, out_dir, progress_hook=progress_hook)
