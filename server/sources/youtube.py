"""YouTube provider — yt-dlp's full extractor for youtube.com / youtu.be.

Wraps the existing `transcriber.download_audio` so the YouTube path is
byte-identical to today's behavior. `resolve()` populates a Source from
yt-dlp's `extract_info(download=False)` for single videos and from
`extract_flat='in_playlist'` for playlists.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL

from .base import Provider, ProviderError, Source, SourceList


# Hosts whose URLs we want to claim as YouTube. Mirrors the regex in
# `transcriber.extract_video_id` (watch / shorts / embed / mobile / music).
_YT_HOSTS = (
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
)

# A loose playlist marker -- if `?list=` is in the URL we treat it as a
# playlist resolution. yt-dlp will tell us definitively via _type='playlist'.
_PLAYLIST_RE = re.compile(r"[?&]list=[A-Za-z0-9_-]+")


class YouTubeProvider(Provider):
    name = "youtube"

    def can_handle(self, url: str) -> bool:
        try:
            host = urlsplit(url).hostname or ""
        except Exception:
            return False
        host = host.lower()
        return any(host == h or host.endswith("." + h) for h in _YT_HOSTS)

    # ------------------------------------------------------------------
    # resolve
    # ------------------------------------------------------------------

    def resolve(self, url: str) -> SourceList:
        # Local imports to avoid module-load-time cycles with transcriber.
        from ..transcriber import _safe_video_id, _ytdlp_cookie_opts, extract_video_id

        # Detect a playlist quickly without doing a full extract for each
        # entry. extract_flat='in_playlist' is what playlist.py already uses.
        if _PLAYLIST_RE.search(url):
            return self._resolve_playlist(url)

        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
        opts.update(_ytdlp_cookie_opts())
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            raise ProviderError(f"yt-dlp failed: {type(e).__name__}: {e}") from e

        if not isinstance(info, dict):
            raise ProviderError("yt-dlp returned no info")

        # Some channel URLs come back as `_type='playlist'`; route there.
        if info.get("_type") == "playlist":
            return self._build_playlist_sourcelist(info)

        vid = info.get("id") or extract_video_id(url) or _safe_video_id(url)
        canonical = f"https://www.youtube.com/watch?v={vid}" if extract_video_id(vid or "") else url
        src = Source(
            kind="youtube",
            url=canonical,
            title=info.get("title") or vid,
            description=(info.get("description") or None),
            duration_sec=info.get("duration"),
            pub_date=info.get("upload_date"),
            image_url=info.get("thumbnail"),
            host=info.get("channel") or info.get("uploader"),
            show_name=None,
            show_url=info.get("channel_url") or info.get("uploader_url"),
            safe_id=vid,
            extra={"yt_info": info},
        )
        return SourceList(
            title=src.title,
            publisher=src.host,
            description=src.description,
            image_url=src.image_url,
            rss_url=None,
            sources=[src],
        )

    def _resolve_playlist(self, url: str) -> SourceList:
        """Use the existing `playlist.preview_playlist` resolver to keep
        behavior consistent with /api/playlist/preview."""
        from .. import playlist as playlist_mod

        try:
            preview = playlist_mod.preview_playlist(url)
        except playlist_mod.PlaylistError as e:
            raise ProviderError(str(e)) from e

        sources: list[Source] = []
        for e in preview.entries:
            sources.append(Source(
                kind="youtube",
                url=e.url,
                title=e.title or e.id,
                description=None,
                duration_sec=e.duration_sec,
                pub_date=None,
                image_url=e.thumbnail_url,
                host=preview.uploader,
                show_name=None,
                show_url=None,
                safe_id=e.id,
                extra={},
            ))
        return SourceList(
            title=preview.title,
            publisher=preview.uploader,
            description=None,
            image_url=None,
            rss_url=None,
            sources=sources,
        )

    def _build_playlist_sourcelist(self, info: dict) -> SourceList:
        """When yt-dlp's extract_info on a single URL reveals it's a
        playlist (channel feed routed via /watch?list=), fall through to
        the same logic as `_resolve_playlist`."""
        sources: list[Source] = []
        for e in info.get("entries") or []:
            if not isinstance(e, dict):
                continue
            vid = e.get("id")
            if not vid:
                continue
            sources.append(Source(
                kind="youtube",
                url=e.get("url") or f"https://youtu.be/{vid}",
                title=e.get("title") or vid,
                description=None,
                duration_sec=e.get("duration"),
                pub_date=None,
                image_url=(e.get("thumbnails") or [{}])[0].get("url") if e.get("thumbnails") else None,
                host=info.get("uploader"),
                show_name=None,
                show_url=None,
                safe_id=vid,
                extra={},
            ))
        return SourceList(
            title=info.get("title"),
            publisher=info.get("uploader"),
            description=None,
            image_url=None,
            rss_url=None,
            sources=sources,
        )

    # ------------------------------------------------------------------
    # download
    # ------------------------------------------------------------------

    def download(
        self,
        source: Source,
        out_dir: Path,
        *,
        progress_hook: Callable[[dict], None] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        # Delegate to the existing `transcriber.download_audio` so the full
        # opts (cookies, retry budget, FFmpegExtractAudio) match today.
        from ..transcriber import download_audio
        return download_audio(source.url, out_dir, progress_hook=progress_hook)
