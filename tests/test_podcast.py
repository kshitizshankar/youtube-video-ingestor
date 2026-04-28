from __future__ import annotations

import io
import json
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from server.podcast import (
    PodcastError,
    _classify,
    _parse_duration,
    _parse_pub_date,
    _parse_rss,
    _pick_episode_by_title,
    _pick_itunes_match,
    preview_podcast,
)


# ---------------------------------------------------------------------------
# A minimal but realistic Buzzsprout-style RSS fixture (~3 episodes).
# ---------------------------------------------------------------------------

RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>The Moonshot Podcast</title>
    <description>Big swings only.</description>
    <itunes:author>Mock Author</itunes:author>
    <image>
      <url>https://img.example.com/show.jpg</url>
      <title>The Moonshot Podcast</title>
      <link>https://example.com</link>
    </image>
    <item>
      <title>Episode 3 - Latest</title>
      <description>Newest one.</description>
      <pubDate>Wed, 02 Apr 2026 10:00:00 +0000</pubDate>
      <guid>ep-3-guid</guid>
      <itunes:duration>01:23:45</itunes:duration>
      <itunes:image href="https://img.example.com/ep3.jpg"/>
      <enclosure url="https://www.buzzsprout.com/abc/ep3.mp3"
                 length="123" type="audio/mpeg"/>
    </item>
    <item>
      <title>Episode 2 - Middle</title>
      <description>Middle one.</description>
      <pubDate>Wed, 12 Mar 2026 10:00:00 +0000</pubDate>
      <guid>ep-2-guid</guid>
      <itunes:duration>2700</itunes:duration>
      <enclosure url="https://www.buzzsprout.com/abc/ep2.mp3"
                 length="456" type="audio/mpeg"/>
    </item>
    <item>
      <title>Episode 1 - First</title>
      <description>The first.</description>
      <pubDate>Wed, 01 Jan 2026 10:00:00 +0000</pubDate>
      <itunes:duration>45:30</itunes:duration>
      <enclosure url="https://www.buzzsprout.com/abc/ep1.mp3"
                 length="789" type="audio/mpeg"/>
    </item>
  </channel>
</rss>
"""


# ---------------------------------------------------------------------------
# RSS parsing
# ---------------------------------------------------------------------------


def test_rss_parse_happy_path():
    p = _parse_rss(RSS_XML, rss_url="https://rss.buzzsprout.com/abc.rss")
    assert p.title == "The Moonshot Podcast"
    assert p.publisher == "Mock Author"
    assert p.description == "Big swings only."
    assert p.image_url == "https://img.example.com/show.jpg"
    assert p.rss_url == "https://rss.buzzsprout.com/abc.rss"
    assert p.source == "rss"
    assert len(p.episodes) == 3
    # Order preserved (newest first, since RSS is reverse-chronological).
    assert p.episodes[0].title == "Episode 3 - Latest"
    assert p.episodes[0].mp3_url == "https://www.buzzsprout.com/abc/ep3.mp3"
    assert p.episodes[0].guid == "ep-3-guid"
    assert p.episodes[0].image_url == "https://img.example.com/ep3.jpg"
    # HH:MM:SS duration parsed.
    assert p.episodes[0].duration_sec == pytest.approx(1 * 3600 + 23 * 60 + 45)
    # Integer-seconds duration parsed.
    assert p.episodes[1].duration_sec == pytest.approx(2700)
    # MM:SS duration parsed.
    assert p.episodes[2].duration_sec == pytest.approx(45 * 60 + 30)
    # Missing guid -> hashed mp3 URL fallback.
    assert p.episodes[2].guid.startswith("mp3-")
    # pubDate normalized to ISO-8601 UTC.
    assert p.episodes[0].pub_date is not None
    assert p.episodes[0].pub_date.startswith("2026-04-02T10:00:00")


def test_rss_truncates_at_cap(monkeypatch):
    # Build an RSS with 250 items; expect 200 + "(truncated...)" note.
    items = "\n".join(
        f"""
        <item>
          <title>Ep {i}</title>
          <enclosure url="https://x.example.com/{i}.mp3" type="audio/mpeg"/>
        </item>
        """
        for i in range(250)
    )
    big = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        b'<channel><title>Big</title>'
        + items.encode("utf-8")
        + b'</channel></rss>'
    )
    p = _parse_rss(big, rss_url=None)
    assert len(p.episodes) == 200
    assert "truncated" in (p.description or "")


def test_rss_invalid_xml_raises():
    with pytest.raises(PodcastError, match="parse error"):
        _parse_rss(b"<<<not xml>>>", rss_url=None)


# ---------------------------------------------------------------------------
# Duration / date helpers
# ---------------------------------------------------------------------------


def test_parse_duration_formats():
    assert _parse_duration("01:23:45") == pytest.approx(5025)
    assert _parse_duration("45:30") == pytest.approx(2730)
    assert _parse_duration("2700") == pytest.approx(2700)
    assert _parse_duration("") is None
    assert _parse_duration(None) is None
    assert _parse_duration("not-a-number") is None


def test_parse_pub_date_iso8601_when_parseable():
    out = _parse_pub_date("Wed, 02 Apr 2026 10:00:00 +0000")
    assert out is not None and out.startswith("2026-04-02T10:00:00")
    # Unparseable -> echo back as-is.
    assert _parse_pub_date("not a date") == "not a date"
    assert _parse_pub_date(None) is None
    assert _parse_pub_date("") is None


# ---------------------------------------------------------------------------
# URL classifier
# ---------------------------------------------------------------------------


def test_classify_routes():
    assert _classify("https://open.spotify.com/show/7r48QL034rSQ9wO1PxGXnu") == "spotify_show"
    # Spotify includes locale prefixes sometimes.
    assert _classify("https://open.spotify.com/intl-de/show/abcdef") == "spotify_show"
    assert _classify("https://open.spotify.com/episode/xyz123") == "spotify_episode"
    assert _classify("https://rss.buzzsprout.com/2406640.rss") == "rss"
    assert _classify("https://example.com/feed.xml") == "rss"
    assert _classify("https://www.buzzsprout.com/abc/ep1.mp3") == "direct_audio"
    assert _classify("https://example.com/audio.m4a") == "direct_audio"
    assert _classify("https://example.com/random/page") == "unknown"


# ---------------------------------------------------------------------------
# preview_podcast dispatch (mocked at urlopen / network boundary)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str | None = None):
        self._body = body
        self._headers = {"Content-Type": content_type} if content_type else {}

    @property
    def headers(self):
        return self._headers

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_urlopen(map_url_to_body):
    """Return a fake urlopen that consults a {url -> bytes} map."""
    def _fake(req, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if url not in map_url_to_body:
            raise AssertionError(f"unexpected URL fetched: {url}")
        body = map_url_to_body[url]
        if isinstance(body, Exception):
            raise body
        return _FakeResponse(body)
    return _fake


def test_preview_rss_dispatch():
    rss_url = "https://rss.buzzsprout.com/abc.rss"
    fake = _make_urlopen({rss_url: RSS_XML})
    with patch("server.podcast.urllib.request.urlopen", side_effect=fake):
        p = preview_podcast(rss_url)
    assert p.source == "rss"
    assert p.title == "The Moonshot Podcast"
    assert len(p.episodes) == 3


def test_preview_direct_audio_dispatch():
    p = preview_podcast("https://www.buzzsprout.com/abc/ep1-foo.mp3")
    assert p.source == "direct_audio"
    assert len(p.episodes) == 1
    assert p.episodes[0].mp3_url == "https://www.buzzsprout.com/abc/ep1-foo.mp3"
    # Title derived from filename, sans extension.
    assert p.episodes[0].title == "ep1-foo"


def _spotify_embed_html(next_data: dict) -> bytes:
    payload = json.dumps(next_data).replace("</", "<\\/")
    html = (
        '<html><head><script id="__NEXT_DATA__" type="application/json">'
        + payload
        + '</script></head></html>'
    )
    return html.encode("utf-8")


def test_preview_spotify_show_dispatch():
    show_id = "7r48QL034rSQ9wO1PxGXnu"
    show_url = f"https://open.spotify.com/show/{show_id}"
    embed_url = f"https://open.spotify.com/embed/show/{show_id}"
    rss_url = "https://rss.example.com/moonshot.rss"
    next_data = {
        "props": {"pageProps": {"state": {"data": {"entity": {
            # Show embeds return the latest episode as `entity` with
            # `subtitle` set to the show name.
            "type": "episode",
            "name": "Episode 3 - Latest",
            "subtitle": "The Moonshot Podcast",
            "relatedEntityCoverArt": [
                {"url": "https://img.spotifycdn.com/show-small.jpg",
                 "maxHeight": 64, "maxWidth": 64},
                {"url": "https://img.spotifycdn.com/show-large.jpg",
                 "maxHeight": 640, "maxWidth": 640},
            ],
        }}}}},
    }
    spotify_html = _spotify_embed_html(next_data)
    itunes_resp = json.dumps({
        "results": [
            {
                "collectionName": "The Moonshot Podcast",
                "feedUrl": rss_url,
                "artistName": "Pep Talks LLC",
                "artworkUrl600": "https://img.itunes.example/600.jpg",
            },
        ],
    }).encode("utf-8")
    itunes_url = (
        "https://itunes.apple.com/search?term=The+Moonshot+Podcast"
        "&entity=podcast&limit=10"
    )
    # When no publisher is exposed by the embed, the resolver first tries an
    # entity=podcastEpisode search using the latest-episode title for
    # disambiguation. We seed it to "miss" (empty results) so the resolver
    # falls back to the plain podcast search.
    itunes_episode_url = (
        "https://itunes.apple.com/search?term=Episode+3+-+Latest"
        "&entity=podcastEpisode&limit=10"
    )
    itunes_episode_resp = json.dumps({"results": []}).encode("utf-8")
    fake = _make_urlopen({
        embed_url: spotify_html,
        itunes_episode_url: itunes_episode_resp,
        itunes_url: itunes_resp,
        rss_url: RSS_XML,
    })
    with patch("server.podcast.urllib.request.urlopen", side_effect=fake):
        p = preview_podcast(show_url)
    assert p.source == "spotify_show"
    assert p.rss_url == rss_url
    assert p.title == "The Moonshot Podcast"
    assert len(p.episodes) == 3
    # Largest cover from Spotify's embed JSON wins over RSS image.
    assert p.image_url == "https://img.spotifycdn.com/show-large.jpg"


def test_preview_spotify_episode_dispatch():
    ep_id = "xyz123"
    ep_url = f"https://open.spotify.com/episode/{ep_id}"
    embed_url = f"https://open.spotify.com/embed/episode/{ep_id}"
    rss_url = "https://rss.example.com/moonshot.rss"
    next_data = {
        "props": {"pageProps": {"state": {"data": {"entity": {
            "type": "episode",
            "name": "Episode 2 - Middle",
            "subtitle": "The Moonshot Podcast",
        }}}}},
    }
    spotify_html = _spotify_embed_html(next_data)
    itunes_resp = json.dumps({
        "results": [
            {
                "collectionName": "The Moonshot Podcast",
                "feedUrl": rss_url,
                "artistName": "Pep Talks LLC",
            },
        ],
    }).encode("utf-8")
    itunes_url = (
        "https://itunes.apple.com/search?term=The+Moonshot+Podcast"
        "&entity=podcast&limit=10"
    )
    # The episode flow also probes entity=podcastEpisode with the episode
    # title before falling back to the show-name search. Seed an empty miss.
    itunes_episode_url = (
        "https://itunes.apple.com/search?term=Episode+2+-+Middle"
        "&entity=podcastEpisode&limit=10"
    )
    itunes_episode_resp = json.dumps({"results": []}).encode("utf-8")
    fake = _make_urlopen({
        embed_url: spotify_html,
        itunes_episode_url: itunes_episode_resp,
        itunes_url: itunes_resp,
        rss_url: RSS_XML,
    })
    with patch("server.podcast.urllib.request.urlopen", side_effect=fake):
        p = preview_podcast(ep_url)
    assert p.source == "spotify_episode"
    assert len(p.episodes) == 1
    assert p.episodes[0].title == "Episode 2 - Middle"
    assert p.episodes[0].mp3_url == "https://www.buzzsprout.com/abc/ep2.mp3"


# ---------------------------------------------------------------------------
# iTunes match logic + miss
# ---------------------------------------------------------------------------


def test_pick_itunes_match_exact():
    res = [
        {"collectionName": "Other Show", "feedUrl": "https://x/o.rss"},
        {"collectionName": "Moonshot", "feedUrl": "https://x/m.rss"},
    ]
    pick = _pick_itunes_match("Moonshot", res)
    assert pick is not None and pick["feedUrl"] == "https://x/m.rss"


def test_pick_itunes_match_word_subset():
    res = [
        {"collectionName": "Some Other Pod", "feedUrl": "https://x/o.rss"},
        {"collectionName": "The Moonshot Podcast Daily", "feedUrl": "https://x/m.rss"},
    ]
    pick = _pick_itunes_match("Moonshot Podcast", res)
    assert pick is not None and pick["feedUrl"] == "https://x/m.rss"


def test_pick_itunes_match_empty():
    assert _pick_itunes_match("anything", []) is None


def test_preview_spotify_show_disambiguates_via_publisher():
    """Regression: two podcasts both named 'The Moonshot Podcast' exist on
    iTunes (Google's X-the-Moonshot-Factory show, and Tatjana Pandurevic's
    show). Without disambiguation iTunes returns Google's first because it
    has more reach. When the Spotify embed exposes a publisher string we
    must (a) include it in the iTunes search term and (b) verify the picked
    result's artistName loosely matches it -- otherwise the resolver will
    silently pick the wrong show."""
    show_id = "7r48QL034rSQ9wO1PxGXnu"
    show_url = f"https://open.spotify.com/show/{show_id}"
    embed_url = f"https://open.spotify.com/embed/show/{show_id}"
    rss_url = "https://rss.buzzsprout.com/2406640.rss"
    next_data = {
        "props": {"pageProps": {"state": {"data": {"entity": {
            "type": "episode",
            "name": "How Lendi Shipped a Customer-Facing AI Agent in 16 Weeks - David Hyman",
            "subtitle": "The Moonshot Podcast",
            # The publisher key the resolver should pick up. (As of this
            # writing Spotify's anonymous embed payload doesn't actually ship
            # this key, but the resolver checks for it under several names so
            # if Spotify ever surfaces it again, disambiguation kicks in.)
            "publisher": "Tatjana Pandurevic",
        }}}}},
    }
    spotify_html = _spotify_embed_html(next_data)
    # iTunes returns BOTH "The Moonshot Podcast" entries. Google's appears
    # first (higher reach), Tatjana's second. The resolver must skip past
    # Google's and pick Tatjana's because only her artistName matches the
    # scraped publisher.
    itunes_resp = json.dumps({
        "results": [
            {
                "collectionName": "The Moonshot Podcast",
                "feedUrl": "https://feeds.megaphone.fm/moonshot",
                "artistName": "X, The Moonshot Factory",
                "artworkUrl600": "https://img.itunes.example/google.jpg",
                "trackCount": 26,
            },
            {
                "collectionName": "The Moonshot Podcast",
                "feedUrl": rss_url,
                "artistName": "Tatjana Pandurevic",
                "artworkUrl600": "https://img.itunes.example/tatjana.jpg",
                "trackCount": 38,
            },
        ],
    }).encode("utf-8")
    # When publisher is known the resolver builds the term as
    # "<show_name> <publisher>" -- not the bare show name.
    itunes_url = (
        "https://itunes.apple.com/search?"
        "term=The+Moonshot+Podcast+Tatjana+Pandurevic"
        "&entity=podcast&limit=10"
    )
    fake = _make_urlopen({
        embed_url: spotify_html,
        itunes_url: itunes_resp,
        rss_url: RSS_XML,
    })
    with patch("server.podcast.urllib.request.urlopen", side_effect=fake):
        p = preview_podcast(show_url)
    # Picked Tatjana's RSS, not Google's.
    assert p.rss_url == rss_url
    assert p.publisher == "Tatjana Pandurevic"
    assert p.title == "The Moonshot Podcast"


def test_preview_spotify_show_raises_when_publisher_has_no_match():
    """If a publisher is known but no candidate from iTunes carries a
    matching artistName, raise PodcastError with the disambiguation hint
    rather than silently falling back to the wrong show."""
    show_url = "https://open.spotify.com/show/zzz"
    embed_url = "https://open.spotify.com/embed/show/zzz"
    next_data = {
        "props": {"pageProps": {"state": {"data": {"entity": {
            "type": "episode",
            "name": "Some episode",
            "subtitle": "The Moonshot Podcast",
            "publisher": "Tatjana Pandurevic",
        }}}}},
    }
    spotify_html = _spotify_embed_html(next_data)
    # Only Google's show is returned from iTunes. None of these match
    # publisher='Tatjana Pandurevic', so resolver must raise.
    itunes_resp = json.dumps({
        "results": [
            {
                "collectionName": "The Moonshot Podcast",
                "feedUrl": "https://feeds.megaphone.fm/moonshot",
                "artistName": "X, The Moonshot Factory",
            },
        ],
    }).encode("utf-8")
    itunes_url_with_pub = (
        "https://itunes.apple.com/search?"
        "term=The+Moonshot+Podcast+Tatjana+Pandurevic"
        "&entity=podcast&limit=10"
    )
    # The resolver also retries without publisher in the term as a safety
    # net (some publisher strings confuse iTunes). Same single Google result.
    itunes_url_plain = (
        "https://itunes.apple.com/search?term=The+Moonshot+Podcast"
        "&entity=podcast&limit=10"
    )
    fake = _make_urlopen({
        embed_url: spotify_html,
        itunes_url_with_pub: itunes_resp,
        itunes_url_plain: itunes_resp,
    })
    with patch("server.podcast.urllib.request.urlopen", side_effect=fake):
        with pytest.raises(PodcastError, match="none from publisher 'Tatjana Pandurevic'"):
            preview_podcast(show_url)


def test_preview_spotify_show_raises_when_itunes_no_match():
    show_url = "https://open.spotify.com/show/zzz"
    embed_url = "https://open.spotify.com/embed/show/zzz"
    next_data = {
        "props": {"pageProps": {"state": {"data": {"entity": {
            "type": "episode",
            "name": "Some episode",
            "subtitle": "ImaginaryShowNobodyHas",
        }}}}},
    }
    spotify_html = _spotify_embed_html(next_data)
    itunes_url = (
        "https://itunes.apple.com/search?term=ImaginaryShowNobodyHas"
        "&entity=podcast&limit=10"
    )
    itunes_episode_url = (
        "https://itunes.apple.com/search?term=Some+episode"
        "&entity=podcastEpisode&limit=10"
    )
    itunes_resp = json.dumps({"results": []}).encode("utf-8")
    fake = _make_urlopen({
        embed_url: spotify_html,
        itunes_episode_url: itunes_resp,
        itunes_url: itunes_resp,
    })
    with patch("server.podcast.urllib.request.urlopen", side_effect=fake):
        with pytest.raises(PodcastError, match="could not find an RSS feed"):
            preview_podcast(show_url)


# ---------------------------------------------------------------------------
# Episode-by-title picker
# ---------------------------------------------------------------------------


def test_pick_episode_by_title_substring():
    p = _parse_rss(RSS_XML, rss_url=None)
    match = _pick_episode_by_title(p.episodes, "Middle")
    assert match is not None and match.title == "Episode 2 - Middle"


def test_pick_episode_by_title_no_match():
    p = _parse_rss(RSS_XML, rss_url=None)
    assert _pick_episode_by_title(p.episodes, "totally absent") is None


# ---------------------------------------------------------------------------
# SSRF gate -- _validate_url rejects non-http schemes, loopback, private IPs,
# link-local (incl. cloud metadata 169.254.169.254), and IPv6 loopback.
# Tested against _validate_url directly so we never hit the network.
# ---------------------------------------------------------------------------


def test_validate_url_rejects_file_scheme():
    from server.podcast import _validate_url
    with pytest.raises(PodcastError, match="internal/private"):
        _validate_url("file:///etc/passwd")


def test_validate_url_rejects_localhost_name():
    from server.podcast import _validate_url
    with pytest.raises(PodcastError, match="internal/private"):
        _validate_url("http://localhost/admin")


def test_validate_url_rejects_loopback_ipv4():
    from server.podcast import _validate_url
    with pytest.raises(PodcastError, match="internal/private"):
        _validate_url("http://127.0.0.1/")


def test_validate_url_rejects_link_local_metadata():
    # AWS / GCP cloud metadata endpoint; link-local must be blocked.
    from server.podcast import _validate_url
    with pytest.raises(PodcastError, match="internal/private"):
        _validate_url("http://169.254.169.254/latest/meta-data/")


def test_validate_url_rejects_loopback_ipv6():
    from server.podcast import _validate_url
    with pytest.raises(PodcastError, match="internal/private"):
        _validate_url("http://[::1]/")


def test_validate_url_rejects_private_ipv4_range():
    from server.podcast import _validate_url
    for u in ("http://10.0.0.1/", "http://192.168.1.1/", "http://172.16.0.1/"):
        with pytest.raises(PodcastError, match="internal/private"):
            _validate_url(u)


def test_validate_url_allows_public_host():
    # google.com resolves to public IPs; should not raise. (Network-light:
    # this hits DNS only, not HTTP.)
    from server.podcast import _validate_url
    _validate_url("https://www.google.com/")


def test_direct_audio_preview_rejects_internal_url():
    # The mp3 URL flows back to the bulk-ingest path; SSRF gate must apply.
    with pytest.raises(PodcastError, match="internal/private"):
        preview_podcast("http://127.0.0.1/secret.mp3")


# ---------------------------------------------------------------------------
# Redirect re-validation -- a benign external URL that 302's to localhost
# must be blocked, not silently followed.
# ---------------------------------------------------------------------------


def test_redirect_to_internal_rejected():
    from server.podcast import _SafeRedirectHandler
    h = _SafeRedirectHandler()
    # We invoke the handler's redirect_request directly. The signature is
    # (req, fp, code, msg, headers, newurl). Raises PodcastError because
    # the new URL targets localhost.
    fake_req = urllib.request.Request("https://benign.example.com/")
    with pytest.raises(PodcastError, match="internal/private"):
        h.redirect_request(fake_req, None, 302, "Found", {}, "http://localhost/admin")


# ---------------------------------------------------------------------------
# Body size cap -- responses larger than MAX_BYTES (16 MB) are rejected.
# ---------------------------------------------------------------------------


class _StreamingFakeResponse:
    """Yields chunks larger than MAX_BYTES on read(n) -> simulates a 17+ MB body."""

    def __init__(self, total_bytes: int, chunk_size: int = 1024 * 1024):
        self._remaining = total_bytes
        self._chunk_size = chunk_size

    @property
    def headers(self):
        return {}

    def read(self, n=None):
        if self._remaining <= 0:
            return b""
        size = self._chunk_size if n is None else min(n, self._chunk_size, self._remaining)
        self._remaining -= size
        return b"x" * size

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_oversize_body_rejected():
    from server.podcast import _safe_urlopen, MAX_BYTES
    # 17 MB stream -- exceeds the 16 MB cap.
    big = MAX_BYTES + 1024 * 1024

    def fake(req, *a, **kw):
        return _StreamingFakeResponse(big)

    with patch("server.podcast.urllib.request.urlopen", side_effect=fake):
        with pytest.raises(PodcastError, match="exceeded"):
            _safe_urlopen("https://big.example.com/feed.rss")


# ---------------------------------------------------------------------------
# <media:content>-only feed -- no <enclosure>, audio URL must be picked up
# from the Media RSS namespace.
# ---------------------------------------------------------------------------


MEDIA_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>MediaContent Podcast</title>
    <description>Anchor-style feed.</description>
    <item>
      <title>Only Episode</title>
      <pubDate>Wed, 02 Apr 2026 10:00:00 +0000</pubDate>
      <guid>media-ep-1</guid>
      <media:content url="https://cdn.example.com/media/ep1.mp3"
                     type="audio/mpeg"
                     duration="1800"/>
    </item>
  </channel>
</rss>
"""


def test_rss_media_content_fallback():
    p = _parse_rss(MEDIA_RSS_XML, rss_url=None)
    assert len(p.episodes) == 1
    assert p.episodes[0].mp3_url == "https://cdn.example.com/media/ep1.mp3"
    assert p.episodes[0].title == "Only Episode"
    assert p.episodes[0].guid == "media-ep-1"


# ---------------------------------------------------------------------------
# Ambiguous show name (multiple iTunes results, no publisher, no episode-
# title hit) must raise rather than silently picking results[0].
# ---------------------------------------------------------------------------


def test_ambiguous_show_name_raises():
    """Generic show name with no publisher and no episode-title match: iTunes
    returns multiple unrelated candidates, none with an exact or word-subset
    title match. Picker must return None; resolver must raise -- not silently
    fall back to results[0]."""
    show_url = "https://open.spotify.com/show/zzz"
    embed_url = "https://open.spotify.com/embed/show/zzz"
    next_data = {
        "props": {"pageProps": {"state": {"data": {"entity": {
            "type": "episode",
            "name": "Some episode",
            "subtitle": "Conversations",
            # No publisher exposed.
        }}}}},
    }
    spotify_html = _spotify_embed_html(next_data)
    # Episode-title search misses (no result whose collectionName contains
    # 'conversations'), so the resolver falls back to the plain podcast
    # search.
    itunes_episode_url = (
        "https://itunes.apple.com/search?term=Some+episode"
        "&entity=podcastEpisode&limit=10"
    )
    itunes_episode_resp = json.dumps({"results": []}).encode("utf-8")
    # Plain podcast search returns multiple results, NONE of whose
    # collectionNames contain the word "conversations" -- so neither the
    # exact-match nor the word-subset path picks one. Picker returns None;
    # resolver raises.
    itunes_url = (
        "https://itunes.apple.com/search?term=Conversations"
        "&entity=podcast&limit=10"
    )
    itunes_resp = json.dumps({
        "results": [
            {"collectionName": "The News Hour", "feedUrl": "https://x/news.rss",
             "artistName": "News Co"},
            {"collectionName": "Tech Talk", "feedUrl": "https://x/tech.rss",
             "artistName": "Tech Inc"},
            {"collectionName": "Story Time", "feedUrl": "https://x/story.rss",
             "artistName": "Story Pub"},
        ],
    }).encode("utf-8")
    fake = _make_urlopen({
        embed_url: spotify_html,
        itunes_episode_url: itunes_episode_resp,
        itunes_url: itunes_resp,
    })
    with patch("server.podcast.urllib.request.urlopen", side_effect=fake):
        with pytest.raises(PodcastError, match="multiple podcasts named 'Conversations'"):
            preview_podcast(show_url)


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("AUTH_DISABLED", "1")
    import importlib
    import server.main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_endpoint_requires_url(client):
    r = client.post("/api/podcast/preview", json={})
    assert r.status_code == 400
    assert "url required" in r.json()["detail"]


def test_endpoint_400_for_unknown_shape(client, monkeypatch):
    import server.podcast as pc

    def boom(url):
        raise pc.PodcastError("unrecognized URL - test")

    monkeypatch.setattr(pc, "preview_podcast", boom)
    # The endpoint imports the module and calls .preview_podcast at request time,
    # so monkeypatching the module attr is sufficient.
    r = client.post("/api/podcast/preview", json={"url": "https://example.com/random"})
    assert r.status_code == 400
    assert "unrecognized URL" in r.json()["detail"]


def test_endpoint_returns_serialized_preview(client, monkeypatch):
    import server.podcast as pc

    fake = pc.PodcastPreview(
        source="rss",
        rss_url="https://x/feed.rss",
        title="Test",
        publisher="Pub",
        description="Desc",
        image_url="https://x/img.jpg",
        episodes=[
            pc.PodcastEpisode(
                guid="g1",
                title="E1",
                description="d",
                pub_date="2026-04-02T10:00:00+00:00",
                duration_sec=600.0,
                mp3_url="https://x/e1.mp3",
                image_url=None,
            ),
        ],
    )
    monkeypatch.setattr(pc, "preview_podcast", lambda url: fake)
    r = client.post("/api/podcast/preview", json={"url": "https://x/feed.rss"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "rss"
    assert body["title"] == "Test"
    assert len(body["episodes"]) == 1
    ep = body["episodes"][0]
    assert ep["mp3_url"] == "https://x/e1.mp3"
    assert ep["duration_sec"] == 600.0
