"""Provider abstraction for ingesting audio/video from any source.

A `Provider` knows how to:
  1. Recognise (`can_handle`) whether it owns a given URL.
  2. Resolve (`resolve`) the URL into one or more `Source` records, each
     carrying the metadata that lands on the videos row + transcript.json.
  3. Download (`download`) a single Source's audio onto disk.

The processing pipeline (queue -> whisperx -> diarize -> analyze) consumes
Sources directly and is provider-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal


# `kind` lands as the `source` column on the videos row. Today's two values
# stay byte-identical with the existing schema ("youtube" / "podcast"); we
# add "audio" for catch-all direct audio URLs that don't belong to a feed.
SourceKind = Literal["youtube", "podcast", "audio"]


@dataclass
class Source:
    """A single ingestable item (one episode, one video).

    Providers return these from `resolve()`. The queue + worker consume
    them, then `persist_video_to_db` reads from the same Source instance to
    write the videos row. The `safe_id` is the on-disk folder name and DB
    primary key; `_safe_video_id()` (in transcriber.py) is the canonical
    derivation.
    """
    kind: SourceKind
    url: str                              # canonical URL for the audio
    title: str
    description: str | None = None
    duration_sec: float | None = None
    pub_date: str | None = None           # ISO-8601 if known
    image_url: str | None = None
    host: str | None = None               # YouTube channel OR podcast publisher
    show_name: str | None = None
    show_url: str | None = None
    safe_id: str | None = None            # filled in by the provider

    # Provider-specific extras the worker may want (e.g. yt-dlp full info
    # dict for YouTube, or the original episode object for diagnostics).
    extra: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any] | None:
        """Materialize the legacy `metadata` dict that
        `persist_video_to_db` + `write_outputs` already understand. Returns
        None for plain YouTube sources so the existing yt-dlp-driven path
        keeps working unchanged."""
        if self.kind == "youtube":
            return None
        return {
            "source": "podcast" if self.kind == "podcast" else "audio",
            "title": self.title,
            "host": self.host,
            "show_name": self.show_name,
            "show_url": self.show_url,
            "image_url": self.image_url,
            "pub_date": self.pub_date,
            "duration_sec": self.duration_sec,
            "description": self.description,
        }


@dataclass
class SourceList:
    """A bundle returned by `Provider.resolve()` when the user URL points
    at a container (podcast show, RSS feed, YouTube playlist).

    Single-source URLs (a single video, a single mp3) return a SourceList
    with one entry in `sources`. The bundle-level metadata describes the
    container; per-item metadata lives on each Source.
    """
    title: str | None
    publisher: str | None
    description: str | None
    image_url: str | None
    rss_url: str | None
    sources: list[Source]


class ProviderError(ValueError):
    """Raised when a URL can't be claimed, resolved, or downloaded."""


class Provider:
    """Abstract base. Subclasses live alongside in this package."""

    name: str = "base"

    def can_handle(self, url: str) -> bool:
        raise NotImplementedError

    def resolve(self, url: str) -> SourceList:
        """URL -> ingestable source(s). For single-item URLs, returns a
        SourceList with one source. Raises ProviderError on failure."""
        raise NotImplementedError

    def download(
        self,
        source: Source,
        out_dir: Path,
        *,
        progress_hook: Callable[[dict], None] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        """Download `source.url` into `out_dir/<source.safe_id>/audio.<ext>`.

        Returns `(audio_path, info_dict)`. `info_dict` carries whatever the
        underlying downloader produced (yt-dlp info, etc.) so the worker
        can pass it to the existing `write_outputs` / `persist_video_to_db`
        helpers without changing their signatures. Raises on failure.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Shared helpers used by multiple providers
# ---------------------------------------------------------------------------


def validate_url(url: str) -> None:
    """Apply the SSRF gate. Reuses `server.podcast._validate_url` so every
    provider's download path uses the exact same allow-list (loopback,
    private, link-local, multicast, reserved, unspecified rejected)."""
    # Local import to avoid a hard module-load-time dependency cycle.
    from .. import podcast as _podcast
    try:
        _podcast._validate_url(url)
    except _podcast.PodcastError as e:
        raise ProviderError(str(e)) from e
