"""Playlist URL -> list of video entries (id, title, duration, thumbnail).
Uses yt-dlp's `extract_flat='in_playlist'` so nothing is downloaded.

Hard cap at 500 entries (spec §15). Channel-feed URLs are rejected with an
explicit error (they'd balloon into thousands). Individual entry failures
(private, region-blocked, deleted) surface in a `failures` list rather than
aborting the preview.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yt_dlp import YoutubeDL

from .transcriber import _ytdlp_cookie_opts

MAX_ENTRIES = 500


@dataclass
class PlaylistEntry:
    id: str
    title: str | None
    duration_sec: float | None
    thumbnail_url: str | None
    url: str


@dataclass
class PlaylistFailure:
    id: str | None
    reason: str


@dataclass
class PlaylistPreview:
    playlist_id: str
    title: str | None
    uploader: str | None
    entry_count: int              # total entries reported by yt-dlp, pre-cap
    entries: list[PlaylistEntry]  # <= MAX_ENTRIES
    failures: list[PlaylistFailure]


class PlaylistError(ValueError):
    """Raised when the URL isn't a playlist or is rejected (too long, channel, etc.)."""


def preview_playlist(url: str, cap: int = MAX_ENTRIES) -> PlaylistPreview:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
    }
    opts.update(_ytdlp_cookie_opts())
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise PlaylistError(f"yt-dlp failed: {type(e).__name__}: {e}")

    if not isinstance(info, dict):
        raise PlaylistError("yt-dlp returned no info")

    kind = info.get("_type")
    # `playlist` is what yt-dlp returns for real playlists. Channel feeds
    # come back as `playlist` too but with a very high entry_count, or
    # sometimes with entries that themselves are `playlist` nested —
    # reject both.
    if kind != "playlist":
        raise PlaylistError(f"not a playlist (yt-dlp kind={kind!r})")

    entries_raw = info.get("entries") or []
    entry_count = len(entries_raw)

    if entry_count > cap:
        raise PlaylistError(
            f"playlist has {entry_count} entries — hard cap is {cap}. "
            "Use a shorter playlist or paste video URLs directly."
        )

    entries: list[PlaylistEntry] = []
    failures: list[PlaylistFailure] = []
    for e in entries_raw:
        if not isinstance(e, dict):
            failures.append(PlaylistFailure(id=None, reason="non-dict entry"))
            continue
        # Detect channel-feed nested playlist entries.
        if e.get("_type") == "playlist":
            raise PlaylistError(
                "this looks like a channel feed, not a playlist — "
                "give me a playlist URL or paste video links instead"
            )
        vid = e.get("id")
        if not vid:
            failures.append(PlaylistFailure(id=None, reason="no id"))
            continue
        # Private / deleted entries sometimes have a title of None and no duration.
        is_unavailable = e.get("availability") in ("private", "needs_auth", "subscriber_only")
        if is_unavailable:
            failures.append(PlaylistFailure(id=vid, reason=str(e.get("availability"))))
            continue
        entries.append(PlaylistEntry(
            id=vid,
            title=e.get("title"),
            duration_sec=e.get("duration"),
            thumbnail_url=(e.get("thumbnails") or [{}])[0].get("url") if e.get("thumbnails") else None,
            url=e.get("url") or f"https://youtu.be/{vid}",
        ))

    return PlaylistPreview(
        playlist_id=info.get("id") or "",
        title=info.get("title"),
        uploader=info.get("uploader"),
        entry_count=entry_count,
        entries=entries,
        failures=failures,
    )
