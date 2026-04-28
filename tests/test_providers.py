"""Provider abstraction tests. Each provider has:
  - can_handle: regex/host/suffix probe (cheap, no network)
  - resolve:    URL -> SourceList (network mocked here)
  - download:   delegates to transcriber.download_audio (covered by
                queue tests + the bulk-ingest integration test)

The dispatch helper picks the first provider whose can_handle returns
True; order matters and is checked here.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from server.sources import (
    GenericAudioProvider,
    Provider,
    ProviderError,
    RSSProvider,
    Source,
    SourceList,
    SpotifyProvider,
    YouTubeProvider,
    all_providers,
    dispatch,
)


# ---------------------------------------------------------------------------
# can_handle: fast path probes
# ---------------------------------------------------------------------------


def test_youtube_provider_recognises_youtube_url():
    p = YouTubeProvider()
    assert p.can_handle("https://www.youtube.com/watch?v=aaaa1111aaa")
    assert p.can_handle("https://youtu.be/aaaa1111aaa")
    assert p.can_handle("https://m.youtube.com/watch?v=aaaa1111aaa")
    assert p.can_handle("https://music.youtube.com/watch?v=aaaa1111aaa")
    # Not a YouTube URL.
    assert p.can_handle("https://open.spotify.com/show/abc") is False
    assert p.can_handle("https://example.com/feed.rss") is False


def test_spotify_provider_recognises_spotify_show():
    p = SpotifyProvider()
    assert p.can_handle("https://open.spotify.com/show/7r48QL034rSQ9wO1PxGXnu")
    assert p.can_handle("https://open.spotify.com/intl-de/show/abcdef")
    assert p.can_handle("https://open.spotify.com/episode/xyz123")
    # YouTube + RSS shouldn't be claimed by Spotify.
    assert p.can_handle("https://www.youtube.com/watch?v=aaaa1111aaa") is False
    assert p.can_handle("https://rss.buzzsprout.com/abc.rss") is False


def test_rss_provider_recognises_rss_url():
    p = RSSProvider()
    assert p.can_handle("https://rss.buzzsprout.com/2406640.rss")
    assert p.can_handle("https://example.com/feed.xml")
    assert p.can_handle("https://example.com/atom.atom")
    # Hits the HEAD-probe path; we patch _http_head_content_type to return
    # nothing so the probe fails closed (returns False).
    with patch(
        "server.podcast._http_head_content_type", return_value=None,
    ):
        assert p.can_handle("https://example.com/page") is False


def test_audio_provider_recognises_mp3_url():
    p = GenericAudioProvider()
    assert p.can_handle("https://www.buzzsprout.com/abc/ep1.mp3")
    assert p.can_handle("https://example.com/audio.m4a")
    assert p.can_handle("https://example.com/audio.wav")
    assert p.can_handle("https://example.com/audio.opus")
    # No extension + HEAD probe fails -> reject.
    with patch(
        "server.podcast._http_head_content_type", return_value=None,
    ):
        assert p.can_handle("https://example.com/page") is False
    # No extension + HEAD says audio/mpeg -> accept.
    with patch(
        "server.podcast._http_head_content_type", return_value="audio/mpeg",
    ):
        assert p.can_handle("https://example.com/no-ext") is True


# ---------------------------------------------------------------------------
# dispatch: order matters
# ---------------------------------------------------------------------------


def test_dispatch_orders_correctly():
    """Mix of URLs each going to its expected provider, in the documented
    order: youtube -> spotify -> rss -> audio."""
    cases = {
        "https://www.youtube.com/watch?v=aaaa1111aaa": YouTubeProvider,
        "https://youtu.be/aaaa1111aaa": YouTubeProvider,
        "https://open.spotify.com/show/7r48QL034rSQ9wO1PxGXnu": SpotifyProvider,
        "https://open.spotify.com/episode/xyz123": SpotifyProvider,
        "https://rss.buzzsprout.com/2406640.rss": RSSProvider,
        "https://example.com/feed.xml": RSSProvider,
        "https://www.buzzsprout.com/abc/ep1.mp3": GenericAudioProvider,
        "https://example.com/foo.m4a": GenericAudioProvider,
    }
    for url, cls in cases.items():
        p = dispatch(url)
        assert isinstance(p, cls), f"{url} -> {type(p).__name__}, expected {cls.__name__}"


def test_dispatch_raises_on_unknown():
    """Plain HTML page with no audio extension and no audio/* MIME -> nobody
    claims it. Patch the HEAD probe so neither RSS nor audio claims it."""
    with patch("server.podcast._http_head_content_type", return_value=None):
        with pytest.raises(ProviderError, match="no provider"):
            dispatch("https://example.com/some/random/page.html")


def test_all_providers_returns_documented_order():
    ps = all_providers()
    # Cheap regex-only providers come first.
    assert isinstance(ps[0], YouTubeProvider)
    assert isinstance(ps[1], SpotifyProvider)
    assert isinstance(ps[2], RSSProvider)
    assert isinstance(ps[3], GenericAudioProvider)


# ---------------------------------------------------------------------------
# resolve: shape sanity (network mocked at the urlopen / yt-dlp boundary)
# ---------------------------------------------------------------------------


# Reuse the same Buzzsprout-style RSS fixture as test_podcast.py so we
# don't duplicate xml parsing logic here.
_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Show</title>
    <description>Desc</description>
    <itunes:author>Author</itunes:author>
    <image><url>https://img.example.com/show.jpg</url></image>
    <item>
      <title>Episode 1</title>
      <description>D1</description>
      <pubDate>Wed, 01 Jan 2026 10:00:00 +0000</pubDate>
      <guid>g1</guid>
      <itunes:duration>1800</itunes:duration>
      <enclosure url="https://cdn.example.com/ep1.mp3" type="audio/mpeg"/>
    </item>
    <item>
      <title>Episode 2</title>
      <pubDate>Wed, 08 Jan 2026 10:00:00 +0000</pubDate>
      <guid>g2</guid>
      <itunes:duration>2400</itunes:duration>
      <enclosure url="https://cdn.example.com/ep2.mp3" type="audio/mpeg"/>
    </item>
  </channel>
</rss>
"""


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    @property
    def headers(self):
        return {}

    def read(self, n=None):
        if n is None:
            return self._body
        out = self._body[:n]
        self._body = self._body[n:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(map_url_to_body):
    def _fake(req, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url not in map_url_to_body:
            raise AssertionError(f"unexpected URL fetched: {url}")
        body = map_url_to_body[url]
        if isinstance(body, Exception):
            raise body
        return _FakeResponse(body)
    return _fake


def test_rss_provider_resolve_returns_sourcelist():
    rss_url = "https://rss.example.com/feed.rss"
    fake = _fake_urlopen({rss_url: _RSS_XML})
    with patch("server.podcast.urllib.request.urlopen", side_effect=fake):
        sl = RSSProvider().resolve(rss_url)
    assert isinstance(sl, SourceList)
    assert sl.title == "Show"
    assert sl.publisher == "Author"
    assert sl.rss_url == rss_url
    assert len(sl.sources) == 2
    s0 = sl.sources[0]
    assert s0.kind == "podcast"
    assert s0.url == "https://cdn.example.com/ep1.mp3"
    assert s0.title == "Episode 1"
    assert s0.duration_sec == pytest.approx(1800)
    assert s0.show_name == "Show"
    assert s0.show_url == rss_url
    assert s0.host == "Author"
    # safe_id is the aud-<sha1[:12]> form.
    assert s0.safe_id and s0.safe_id.startswith("aud-")
    # to_metadata() round-trips into the legacy podcast-metadata dict.
    md = s0.to_metadata()
    assert md is not None
    assert md["source"] == "podcast"
    assert md["title"] == "Episode 1"
    assert md["host"] == "Author"
    assert md["show_name"] == "Show"
    assert md["pub_date"].startswith("2026-01-01T10:00:00")


def test_audio_provider_resolve_returns_one_source():
    """Direct .mp3 URLs come back as one Source with title derived from the
    filename and no other metadata."""
    sl = GenericAudioProvider().resolve("https://example.com/some/episode-001.mp3")
    assert len(sl.sources) == 1
    s = sl.sources[0]
    assert s.kind == "audio"
    assert s.url == "https://example.com/some/episode-001.mp3"
    assert s.title == "episode-001"
    assert s.host is None
    assert s.show_name is None
    assert s.safe_id and s.safe_id.startswith("aud-")
    # YouTube source path is the only one whose to_metadata() returns None;
    # everything else gets a podcast-shaped dict.
    md = s.to_metadata()
    assert md is not None
    assert md["source"] == "audio"


def test_audio_provider_resolve_rejects_internal_url():
    """SSRF gate fires before any download."""
    with pytest.raises(ProviderError, match="internal/private"):
        GenericAudioProvider().resolve("http://127.0.0.1/secret.mp3")


def test_spotify_provider_resolve_returns_sourcelist(monkeypatch):
    """SpotifyProvider.resolve delegates to podcast.preview_podcast. We
    monkeypatch that boundary so we don't have to mock embed scraping +
    iTunes search + RSS in this test."""
    import server.podcast as pc
    fake = pc.PodcastPreview(
        source="spotify_show",
        rss_url="https://rss.example.com/feed.rss",
        title="Show",
        publisher="Pub",
        description="Desc",
        image_url="https://img.example.com/cover.jpg",
        episodes=[
            pc.PodcastEpisode(
                guid="g1",
                title="Episode 1",
                description="D1",
                pub_date="2026-01-01T10:00:00+00:00",
                duration_sec=1800,
                mp3_url="https://cdn.example.com/ep1.mp3",
                image_url=None,
            ),
        ],
    )
    monkeypatch.setattr(pc, "preview_podcast", lambda url: fake)

    sl = SpotifyProvider().resolve("https://open.spotify.com/show/abc")
    assert sl.title == "Show"
    assert sl.publisher == "Pub"
    assert len(sl.sources) == 1
    s = sl.sources[0]
    assert s.kind == "podcast"
    assert s.title == "Episode 1"
    assert s.show_name == "Show"
    assert s.show_url == "https://rss.example.com/feed.rss"
    # Episode without its own image_url falls back to the show image.
    assert s.image_url == "https://img.example.com/cover.jpg"


def test_spotify_provider_resolve_translates_podcast_error(monkeypatch):
    import server.podcast as pc

    def boom(url):
        raise pc.PodcastError("could not extract show name")

    monkeypatch.setattr(pc, "preview_podcast", boom)
    with pytest.raises(ProviderError, match="show name"):
        SpotifyProvider().resolve("https://open.spotify.com/show/abc")


def test_youtube_provider_resolve_single_video(monkeypatch):
    """yt-dlp is mocked so this stays offline."""
    import server.sources.youtube as yt_mod

    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            captured["url"] = url
            return {
                "id": "aaaa1111aaa",
                "title": "Test Video",
                "description": "A YouTube description.",
                "duration": 425.0,
                "upload_date": "20260201",
                "thumbnail": "https://i.ytimg.com/x.jpg",
                "channel": "TestChan",
                "channel_url": "https://www.youtube.com/@TestChan",
            }

    monkeypatch.setattr(yt_mod, "YoutubeDL", FakeYDL)

    sl = YouTubeProvider().resolve("https://youtu.be/aaaa1111aaa")
    assert len(sl.sources) == 1
    s = sl.sources[0]
    assert s.kind == "youtube"
    assert s.safe_id == "aaaa1111aaa"
    assert s.title == "Test Video"
    assert s.duration_sec == 425.0
    assert s.host == "TestChan"
    assert s.show_url == "https://www.youtube.com/@TestChan"
    # YouTube to_metadata() is None -> the legacy yt-dlp-driven write path
    # stays in charge for these.
    assert s.to_metadata() is None


# ---------------------------------------------------------------------------
# Source dataclass shape
# ---------------------------------------------------------------------------


def test_source_to_metadata_youtube_returns_none():
    s = Source(kind="youtube", url="https://x", title="t", safe_id="abc")
    assert s.to_metadata() is None


def test_source_to_metadata_podcast_shape_matches_legacy():
    """The legacy `metadata` dict is what TranscribeRequest.metadata
    expects today; persist_video_to_db / write_outputs both consume that
    shape. Source.to_metadata() must produce a byte-compatible dict."""
    s = Source(
        kind="podcast",
        url="https://cdn/ep1.mp3",
        title="Ep 1",
        description="d",
        duration_sec=1800.0,
        pub_date="2026-01-01T10:00:00+00:00",
        image_url="https://img/show.jpg",
        host="Pub",
        show_name="Show",
        show_url="https://rss/feed.rss",
        safe_id="aud-deadbeef",
    )
    md = s.to_metadata()
    assert md == {
        "source": "podcast",
        "title": "Ep 1",
        "host": "Pub",
        "show_name": "Show",
        "show_url": "https://rss/feed.rss",
        "image_url": "https://img/show.jpg",
        "pub_date": "2026-01-01T10:00:00+00:00",
        "duration_sec": 1800.0,
        "description": "d",
    }


# ---------------------------------------------------------------------------
# Bulk ingest dispatch path (no network -- monkeypatch enqueue + dispatch).
# Confirms a Spotify show URL pasted into /api/ingests resolves into N
# episodes, each enqueued with metadata.
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("AUTH_DISABLED", "1")
    import importlib
    import server.main as main_mod
    importlib.reload(main_mod)
    from fastapi.testclient import TestClient
    return TestClient(main_mod.app)


def test_bulk_ingest_dispatches_spotify_to_episodes(client, monkeypatch):
    """Pasting a Spotify show URL into POST /api/ingests should resolve
    into multiple Source records and enqueue each one. Smoke-tests the
    new dispatch path without any real network."""
    import server.sources.spotify as sp_mod
    import server.podcast as pc

    fake_preview = pc.PodcastPreview(
        source="spotify_show",
        rss_url="https://rss.example.com/feed.rss",
        title="Show",
        publisher="Pub",
        description="Desc",
        image_url="https://img.example.com/cover.jpg",
        episodes=[
            pc.PodcastEpisode(
                guid=f"g{i}",
                title=f"Episode {i}",
                description=f"D{i}",
                pub_date=None,
                duration_sec=1800.0,
                mp3_url=f"https://cdn.example.com/ep{i}.mp3",
                image_url=None,
            )
            for i in (1, 2, 3)
        ],
    )
    monkeypatch.setattr(pc, "preview_podcast", lambda url: fake_preview)

    captured = []

    def fake_enqueue(req, out_dir, hf_token=None, project_id=None):
        captured.append({"url": req.url, "metadata": req.metadata})

    import server.main as main_mod
    monkeypatch.setattr(main_mod.queue_mod, "enqueue_ingest", fake_enqueue)

    r = client.post(
        "/api/ingests",
        json={"urls": ["https://open.spotify.com/show/abc"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["job_ids"]) == 3
    assert all(jid.startswith("aud-") for jid in body["job_ids"])
    assert len(captured) == 3
    # Each enqueue saw a podcast-shaped metadata dict.
    for i, cap in enumerate(captured, start=1):
        assert cap["url"] == f"https://cdn.example.com/ep{i}.mp3"
        md = cap["metadata"]
        assert md is not None
        assert md["source"] == "podcast"
        assert md["title"] == f"Episode {i}"
        assert md["show_name"] == "Show"
