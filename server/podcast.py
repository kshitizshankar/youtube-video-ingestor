"""Podcast URL -> list of episodes for the ingest preview UI.

Mirrors the playlist preview pattern (see playlist.py). The user pastes one
of:

  1. open.spotify.com/show/<id>      -> resolve to RSS via iTunes Search
  2. open.spotify.com/episode/<id>   -> resolve to RSS via iTunes Search,
                                        then pick the matching episode
  3. <something>.rss                 -> parse RSS directly
  4. <direct .mp3 URL>               -> single-episode synthetic preview

Spotify itself doesn't expose episode audio, so for #1 and #2 we scrape the
public show/episode page for the title (og:title), query iTunes Search API
for that podcast name, take the first matching `feedUrl` and parse it as a
plain RSS feed. The `<enclosure url=...mp3>` URLs come straight from the
publisher's CDN (Buzzsprout, Megaphone, Libsyn, etc.) and are downloadable
by yt-dlp's generic extractor, which sends a real browser User-Agent.

stdlib only: urllib + xml.etree.ElementTree.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal


MAX_EPISODES = 200

# Hard cap on any single response body we're willing to buffer in memory.
# 16 MB is more than enough for an RSS feed or Spotify embed page; an audio
# file fetched server-side via the bulk-ingest path is handled by yt-dlp,
# not this module.
MAX_BYTES = 16 * 1024 * 1024

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"

# Media RSS namespace -- some feeds (Anchor variants, WordPress podcast
# plugins, Atom-formatted feeds) put audio in <media:content> instead of
# <enclosure>.
MEDIA_NS = "http://search.yahoo.com/mrss/"

# A real-looking browser User-Agent. Spotify (and some podcast CDNs) reject
# obvious bot UAs with 403/406.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Network timeout for all HTTP probes (seconds). Generous because iTunes
# Search and Spotify can be sluggish.
_HTTP_TIMEOUT = 15.0

# Uniform reject message for SSRF-gate hits. Surfaced to the user; do NOT
# include the offending URL or hostname.
_SSRF_REJECT_MSG = (
    "URL points to an internal/private address - refusing to fetch"
)


# ---------------------------------------------------------------------------
# Public dataclasses + error type
# ---------------------------------------------------------------------------


@dataclass
class PodcastEpisode:
    guid: str
    title: str
    description: str | None
    pub_date: str | None       # ISO-8601 if parseable, else original string
    duration_sec: float | None
    mp3_url: str
    image_url: str | None


@dataclass
class PodcastPreview:
    source: Literal["spotify_show", "spotify_episode", "rss", "direct_audio"]
    rss_url: str | None
    title: str | None
    publisher: str | None
    description: str | None
    image_url: str | None
    episodes: list[PodcastEpisode]


class PodcastError(ValueError):
    """Raised when the URL can't be resolved to a podcast feed/episode."""


# ---------------------------------------------------------------------------
# URL classification
# ---------------------------------------------------------------------------


_SPOTIFY_SHOW_RE = re.compile(
    r"^https?://open\.spotify\.com/(?:[a-z-]+/)?show/([A-Za-z0-9]+)"
)
_SPOTIFY_EPISODE_RE = re.compile(
    r"^https?://open\.spotify\.com/(?:[a-z-]+/)?episode/([A-Za-z0-9]+)"
)


def _classify(url: str) -> str:
    u = url.strip()
    if _SPOTIFY_SHOW_RE.match(u):
        return "spotify_show"
    if _SPOTIFY_EPISODE_RE.match(u):
        return "spotify_episode"
    # Strip query/fragment for the suffix probe.
    path = urllib.parse.urlsplit(u).path.lower()
    if path.endswith(".rss") or path.endswith(".xml"):
        return "rss"
    if path.endswith(".mp3") or path.endswith(".m4a") or path.endswith(".aac") or path.endswith(".wav"):
        return "direct_audio"
    return "unknown"


# ---------------------------------------------------------------------------
# SSRF gate -- reject schemes other than http(s), bare/loopback/private IPs,
# DNS names that resolve to any non-public address, and redirects whose
# Location lands somewhere internal. Applies to every server-side fetch this
# module performs AND to any audio URL we hand back to the bulk-ingest path
# (yt-dlp will fetch that one for us, so it has to clear the same gate).
# ---------------------------------------------------------------------------


def _ip_is_disallowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Block loopback, private, link-local, multicast, reserved, unspecified.
    Catches 127.0.0.1, 10/8, 192.168/16, 172.16/12, 169.254/16, ::1, fc00::/7,
    fe80::/10, etc. -- and the cloud metadata endpoint 169.254.169.254."""
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_url(url: str) -> None:
    """Raise PodcastError if `url` is anything other than an http(s) URL whose
    host resolves entirely to public, routable IPs. Called at the top of every
    network operation AND on every redirect Location."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise PodcastError(_SSRF_REJECT_MSG)
    host = parsed.hostname
    if not host:
        raise PodcastError(_SSRF_REJECT_MSG)

    # If the host is itself an IP literal (with or without IPv6 brackets),
    # validate it directly. urlsplit().hostname strips IPv6 brackets for us.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _ip_is_disallowed(ip):
            raise PodcastError(_SSRF_REJECT_MSG)
        return

    # DNS hostname: resolve to ALL addresses and reject if ANY is non-public.
    # This catches DNS-rebinding-style configurations where a public name
    # resolves to a private IP. getaddrinfo with AF_UNSPEC returns both v4
    # and v6 results.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Let the actual fetch surface the network error -- we don't want to
        # turn a transient DNS failure into a confusing SSRF reject.
        return
    for info in infos:
        sockaddr = info[4]
        addr_str = sockaddr[0]
        # IPv6 scoped addresses can include a "%scope" suffix; strip it.
        if "%" in addr_str:
            addr_str = addr_str.split("%", 1)[0]
        try:
            resolved = ipaddress.ip_address(addr_str)
        except ValueError:
            continue
        if _ip_is_disallowed(resolved):
            raise PodcastError(_SSRF_REJECT_MSG)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTPRedirectHandler that re-runs `_validate_url` on the redirect
    Location. A benign external URL that 302's to http://localhost/admin must
    be blocked."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Validate BEFORE delegating to the parent so we raise our own error
        # rather than letting urllib follow the redirect.
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Install our redirect-validating handler as the default opener. After this
# call, `urllib.request.urlopen(...)` re-validates every redirect Location
# through `_validate_url` -- a benign external URL can't 302 to localhost.
# Tests patch `server.podcast.urllib.request.urlopen` directly, which short-
# circuits the opener entirely (so the install is a no-op for tests).
urllib.request.install_opener(
    urllib.request.build_opener(_SafeRedirectHandler())
)


# ---------------------------------------------------------------------------
# Low-level HTTP helpers (urllib only, all flow through the SSRF gate)
# ---------------------------------------------------------------------------


def _safe_urlopen(url: str, *, accept: str | None = None) -> bytes:
    """Validate, fetch, and return up to MAX_BYTES of response body.

    All server-side HTTP in this module goes through here so the SSRF gate,
    redirect re-validation, hard timeout, and body-size cap are uniform.
    Raises PodcastError on any failure; error messages never include the URL
    so the FastAPI layer can surface them safely to clients.
    """
    _validate_url(url)
    headers = {"User-Agent": _USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        # `urlopen` uses the installed default opener (set above), so the
        # redirect handler re-validates every Location. Hard 20s timeout
        # covers connect+read.
        with urllib.request.urlopen(req, timeout=20) as resp:
            buf = bytearray()
            try:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        raise PodcastError(
                            f"response exceeded {MAX_BYTES // (1024 * 1024)} MB cap"
                        )
            except TypeError:
                # Fallback for response objects whose `read()` doesn't accept
                # a size argument (some test stubs). Still enforce the cap.
                buf = bytearray(resp.read())
                if len(buf) > MAX_BYTES:
                    raise PodcastError(
                        f"response exceeded {MAX_BYTES // (1024 * 1024)} MB cap"
                    )
            return bytes(buf)
    except PodcastError:
        raise
    except AssertionError:
        # Surface programming errors / test stubs verbatim instead of
        # masking them as "upstream fetch failed".
        raise
    except Exception as e:
        # Scrub URL from the error message; keep the exception type only.
        raise PodcastError(f"upstream fetch failed ({type(e).__name__})")


def _http_get(url: str, *, accept: str | None = None) -> bytes:
    return _safe_urlopen(url, accept=accept)


def _http_head_content_type(url: str) -> str | None:
    """HEAD probe; returns the Content-Type header lowercased, or None on error.
    Some CDNs reject HEAD (405) -- fall back to a tiny ranged GET in that
    case. Both probes flow through the SSRF gate.
    """
    _validate_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            ct = resp.headers.get("Content-Type")
            return ct.lower() if ct else None
    except PodcastError:
        # Re-raise SSRF rejects -- caller should see them, not silently drop.
        raise
    except Exception:
        # Fall back to ranged GET (1 byte) -- keeps it cheap.
        try:
            req2 = urllib.request.Request(
                url,
                headers={"User-Agent": _USER_AGENT, "Range": "bytes=0-0"},
            )
            with urllib.request.urlopen(req2, timeout=20) as resp:
                ct = resp.headers.get("Content-Type")
                return ct.lower() if ct else None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Spotify scraping
# ---------------------------------------------------------------------------


_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _spotify_to_embed_url(url: str) -> str:
    """Translate a public Spotify URL to its /embed/ counterpart, which serves
    the page's structured data inside <script id="__NEXT_DATA__"> as JSON.
    Plain `open.spotify.com/show/<id>` is a SPA shell with no metadata."""
    m = _SPOTIFY_SHOW_RE.match(url)
    if m:
        return f"https://open.spotify.com/embed/show/{m.group(1)}"
    m = _SPOTIFY_EPISODE_RE.match(url)
    if m:
        return f"https://open.spotify.com/embed/episode/{m.group(1)}"
    return url


def _largest_image(images: Any) -> str | None:
    """Pick the largest entry from Spotify's [{url, maxHeight, maxWidth}] list."""
    if not isinstance(images, list) or not images:
        return None
    best = None
    best_area = -1
    for img in images:
        if not isinstance(img, dict):
            continue
        u = img.get("url")
        if not u:
            continue
        area = int(img.get("maxHeight") or 0) * int(img.get("maxWidth") or 0)
        if area > best_area:
            best_area = area
            best = u
    return best


def _extract_publisher(entity: dict) -> str | None:
    """Look for a publisher string under the various keys Spotify has used
    over time. As of this writing the public embed payload does NOT include
    publisher under any key on most shows -- but if Spotify ever surfaces it
    again, we want to pick it up automatically.

    Checked locations (in order):
      entity.publisher
      entity.creator
      entity.show.publisher           (when entity is an episode wrapping a show)
      entity.attributes.show.publisher
      entity.relatedEntity.publisher
    """
    if not isinstance(entity, dict):
        return None
    candidates = [
        entity.get("publisher"),
        entity.get("creator"),
        ((entity.get("show") or {}) if isinstance(entity.get("show"), dict) else {}).get("publisher"),
        (((entity.get("attributes") or {}).get("show") or {})
         if isinstance(entity.get("attributes"), dict) else {}).get("publisher"),
        ((entity.get("relatedEntity") or {}) if isinstance(entity.get("relatedEntity"), dict) else {}).get("publisher"),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def _scrape_spotify(url: str) -> dict[str, str | None]:
    """Fetch the embed page and extract structured fields from __NEXT_DATA__.
    Falls back to og:title / og:image scraping if the JSON shape is unexpected.

    Returned keys (any/all may be None):
      entity_type   - "show" or "episode"
      entity_name   - the entity's own title (show name OR episode name)
      show_name     - for episode pages, the parent show's name (from `subtitle`)
      publisher     - publisher / creator string, if exposed by the embed JSON
      cover_url     - largest cover-art URL
    """
    embed_url = _spotify_to_embed_url(url)
    html = _http_get(embed_url, accept="text/html").decode("utf-8", errors="replace")

    out: dict[str, str | None] = {
        "entity_type": None,
        "entity_name": None,
        "show_name": None,
        "publisher": None,
        "cover_url": None,
    }

    m = _NEXT_DATA_RE.search(html)
    if m:
        try:
            data = json.loads(m.group(1))
            entity = (
                data.get("props", {})
                .get("pageProps", {})
                .get("state", {})
                .get("data", {})
                .get("entity")
                or {}
            )
            etype = entity.get("type")
            ename = entity.get("name") or entity.get("title")
            subtitle = entity.get("subtitle")
            cover = _largest_image(entity.get("relatedEntityCoverArt"))
            if not cover:
                cover = _largest_image(
                    (entity.get("visualIdentity") or {}).get("image")
                )
            out["entity_type"] = etype
            out["entity_name"] = ename
            # For episode entities, `subtitle` is the show name.
            # For show entities, the embed actually returns the latest
            # episode (with subtitle=show name and relatedEntityUri pointing
            # at the show). Either way `subtitle` is reliably the show name.
            if subtitle:
                out["show_name"] = subtitle
            out["publisher"] = _extract_publisher(entity)
            out["cover_url"] = cover
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

    # Fallback: og:title / og:image scraping (most Spotify embed pages don't
    # serve these, but harmless to try).
    if not out["entity_name"]:
        og_title = _OG_TITLE_RE.search(html)
        if og_title:
            out["entity_name"] = og_title.group(1).strip()
    if not out["cover_url"]:
        og_image = _OG_IMAGE_RE.search(html)
        if og_image:
            out["cover_url"] = og_image.group(1).strip()

    return out


# ---------------------------------------------------------------------------
# iTunes Search API
# ---------------------------------------------------------------------------


def _itunes_search_podcast(name: str, publisher: str | None = None) -> list[dict[str, Any]]:
    """Search iTunes for podcasts. When publisher is supplied, include it in
    the term -- iTunes ranks by relevance, so the right show jumps to the top
    when multiple podcasts share the same title (eg. two "The Moonshot Podcast"
    entries: Google's vs Tatjana Pandurevic's)."""
    term = f"{name} {publisher}".strip() if publisher else name
    q = urllib.parse.urlencode({"term": term, "entity": "podcast", "limit": 10})
    raw = _http_get(f"https://itunes.apple.com/search?{q}", accept="application/json")
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        raise PodcastError("iTunes Search returned non-JSON")
    return list(data.get("results") or [])


def _itunes_search_episode(episode_title: str) -> list[dict[str, Any]]:
    """Search iTunes for a specific podcast episode by its title. Each result
    carries the parent show's collectionName + feedUrl, which lets us resolve
    the RSS even when the show name is ambiguous and we have no publisher."""
    q = urllib.parse.urlencode({"term": episode_title, "entity": "podcastEpisode", "limit": 10})
    raw = _http_get(f"https://itunes.apple.com/search?{q}", accept="application/json")
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        raise PodcastError("iTunes Search returned non-JSON")
    return list(data.get("results") or [])


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _publisher_matches(itunes_artist: str | None, publisher: str | None) -> bool:
    """Loose match: case-insensitive, ignore extra whitespace, and accept
    either string being a substring of the other (so 'X' matches
    'X, The Moonshot Factory' but 'Tatjana Pandurevic' does not)."""
    if not publisher or not itunes_artist:
        return False
    a = _normalize(itunes_artist)
    p = _normalize(publisher)
    if not a or not p:
        return False
    return a == p or a in p or p in a


def _pick_itunes_match(
    name: str,
    results: list[dict[str, Any]],
    publisher: str | None = None,
) -> dict[str, Any] | None:
    if not results:
        return None
    target = _normalize(name)

    # When we have a publisher, prefer results whose artistName matches it AND
    # whose collectionName matches the show name. iTunes uses `artistName` for
    # the podcast's host/publisher.
    if publisher:
        # 1a. Exact title match + publisher match.
        for r in results:
            cn = r.get("collectionName") or ""
            if _normalize(cn) == target and _publisher_matches(r.get("artistName"), publisher):
                return r
        # 1b. All query words contained in collectionName + publisher match.
        target_words = [w for w in target.split() if w]
        if target_words:
            for r in results:
                cn = _normalize(r.get("collectionName") or "")
                if (
                    all(w in cn for w in target_words)
                    and _publisher_matches(r.get("artistName"), publisher)
                ):
                    return r
        # 1c. Any result with a matching publisher, regardless of title fuzz.
        for r in results:
            if _publisher_matches(r.get("artistName"), publisher):
                return r
        # No publisher match found in this batch -- signal the caller.
        return None

    # No publisher supplied: original behavior.
    # 1. Exact (case-insensitive) match on collectionName.
    for r in results:
        cn = r.get("collectionName") or ""
        if _normalize(cn) == target:
            return r
    # 2. All query words contained in collectionName.
    target_words = [w for w in target.split() if w]
    if target_words:
        for r in results:
            cn = _normalize(r.get("collectionName") or "")
            if all(w in cn for w in target_words):
                return r
    # 3. Last resort: only accept when iTunes returned exactly one candidate.
    #    For generic show names ("Daily", "Conversations") iTunes returns ten
    #    unrelated podcasts; silently picking results[0] masks ambiguity. Let
    #    the caller raise so the user can disambiguate by pasting the RSS URL.
    if len(results) == 1:
        return results[0]
    return None


def _resolve_rss_via_itunes(
    name: str,
    publisher: str | None = None,
    *,
    episode_title: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return (rss_url, itunes_record). Raises PodcastError on miss.

    Disambiguation strategy when the show name is ambiguous (eg. multiple
    podcasts share a title):

      1. If `publisher` is known, query iTunes with `<name> <publisher>` and
         require the picked result's `artistName` to loosely match.
      2. Else if an `episode_title` from the parent show is known, query
         iTunes for that specific episode (entity=podcastEpisode) -- the
         result's collectionName + feedUrl uniquely identify the show.
      3. Else fall back to a plain show-name search and take the best fuzzy
         match. This is the original behavior and works for unique titles.
    """
    if publisher:
        results = _itunes_search_podcast(name, publisher=publisher)
        pick = _pick_itunes_match(name, results, publisher=publisher)
        if pick and pick.get("feedUrl"):
            return str(pick["feedUrl"]), pick
        # Search again without publisher in the term -- iTunes sometimes
        # returns nothing when the publisher string is messy.
        results_plain = _itunes_search_podcast(name)
        pick = _pick_itunes_match(name, results_plain, publisher=publisher)
        if pick and pick.get("feedUrl"):
            return str(pick["feedUrl"]), pick
        n_total = len(results_plain) or len(results)
        raise PodcastError(
            f"found {n_total} podcasts named '{name}' but none from publisher "
            f"'{publisher}' - paste the RSS URL directly"
        )

    if episode_title:
        # Find the show via one of its recent episodes. This works even when
        # publisher is unknown because the episode title is usually unique.
        ep_results = _itunes_search_episode(episode_title)
        target = _normalize(name)
        for r in ep_results:
            cn = _normalize(r.get("collectionName") or "")
            if cn == target and r.get("feedUrl"):
                return str(r["feedUrl"]), r
        # Loosened: any episode result whose collection contains all query
        # words.
        target_words = [w for w in target.split() if w]
        if target_words:
            for r in ep_results:
                cn = _normalize(r.get("collectionName") or "")
                if all(w in cn for w in target_words) and r.get("feedUrl"):
                    return str(r["feedUrl"]), r

    results = _itunes_search_podcast(name)
    pick = _pick_itunes_match(name, results)
    if pick is None and results:
        # Ambiguous: multiple iTunes candidates, none uniquely matched.
        raise PodcastError(
            f"multiple podcasts named '{name}' on iTunes - paste the RSS URL "
            f"directly to disambiguate"
        )
    if not pick or not pick.get("feedUrl"):
        raise PodcastError(
            f"could not find an RSS feed for show '{name}' "
            "- try pasting the RSS URL directly"
        )
    return str(pick["feedUrl"]), pick


# ---------------------------------------------------------------------------
# RSS parsing
# ---------------------------------------------------------------------------


def _itunes_tag(local: str) -> str:
    return f"{{{ITUNES_NS}}}{local}"


def _parse_pub_date(raw: str | None) -> str | None:
    """RSS pubDate is RFC 822. Convert to ISO-8601 if parseable, else echo raw."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except Exception:
        return raw
    if dt is None:
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return raw


def _parse_duration(raw: str | None) -> float | None:
    """itunes:duration is one of: 'HH:MM:SS', 'MM:SS', or integer seconds."""
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    if ":" in s:
        parts = s.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 3:
            h, m, sec = nums
            return h * 3600 + m * 60 + sec
        if len(nums) == 2:
            m, sec = nums
            return m * 60 + sec
        if len(nums) == 1:
            return nums[0]
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _episode_guid(item: ET.Element, mp3_url: str) -> str:
    g = item.findtext("guid")
    if g and g.strip():
        return g.strip()
    return "mp3-" + hashlib.sha1(mp3_url.encode("utf-8")).hexdigest()[:16]


def _parse_rss(xml_bytes: bytes, *, rss_url: str | None) -> PodcastPreview:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise PodcastError(f"RSS parse error: {e}")

    # Most podcast RSS feeds use <rss><channel>...</channel></rss>; some use
    # an Atom feed under a different namespace. We only support the RSS form.
    channel = root.find("channel")
    if channel is None:
        # ElementTree may have stripped the wrapper if root is already <channel>.
        if root.tag.endswith("channel"):
            channel = root
        else:
            raise PodcastError("RSS feed has no <channel> element")

    title = (channel.findtext("title") or "").strip() or None
    description = (channel.findtext("description") or "").strip() or None
    publisher = (channel.findtext(_itunes_tag("author")) or "").strip() or None

    image_url: str | None = None
    img = channel.find("image")
    if img is not None:
        image_url = (img.findtext("url") or "").strip() or None
    if not image_url:
        # itunes:image href="..."
        itunes_img = channel.find(_itunes_tag("image"))
        if itunes_img is not None:
            image_url = itunes_img.attrib.get("href") or None

    items = channel.findall("item")
    truncated = False
    if len(items) > MAX_EPISODES:
        items = items[:MAX_EPISODES]
        truncated = True

    episodes: list[PodcastEpisode] = []
    for item in items:
        # Primary: standard RSS <enclosure url="..." type="audio/...">
        mp3_url = ""
        enclosure = item.find("enclosure")
        if enclosure is not None:
            mp3_url = (enclosure.attrib.get("url") or "").strip()
        # Fallback: Media RSS <media:content url="..." type="audio/..."/>.
        # Some Anchor configs, WordPress podcast plugins, and Atom-formatted
        # feeds put audio here instead. Without this fallback those feeds
        # silently yield episodes=[].
        if not mp3_url:
            mc = item.find(f"{{{MEDIA_NS}}}content")
            if mc is not None and (mc.attrib.get("type") or "").startswith("audio"):
                mp3_url = (mc.attrib.get("url") or "").strip()
        if not mp3_url:
            continue
        # The mp3_url gets handed back to the bulk-ingest path which fetches
        # it server-side (yt-dlp). Apply the same SSRF gate as our own
        # fetches; silently drop episodes whose audio URL points at an
        # internal/private address.
        try:
            _validate_url(mp3_url)
        except PodcastError:
            continue
        ep_title = (item.findtext("title") or "").strip() or "(untitled)"
        ep_desc = (item.findtext("description") or "").strip() or None
        ep_pub = _parse_pub_date(item.findtext("pubDate"))
        ep_dur = _parse_duration(item.findtext(_itunes_tag("duration")))
        ep_image: str | None = None
        ii = item.find(_itunes_tag("image"))
        if ii is not None:
            ep_image = ii.attrib.get("href") or None
        episodes.append(PodcastEpisode(
            guid=_episode_guid(item, mp3_url),
            title=ep_title,
            description=ep_desc,
            pub_date=ep_pub,
            duration_sec=ep_dur,
            mp3_url=mp3_url,
            image_url=ep_image,
        ))

    if truncated:
        note = f"(truncated to {MAX_EPISODES} most recent episodes)"
        description = f"{description}\n\n{note}" if description else note

    return PodcastPreview(
        source="rss",
        rss_url=rss_url,
        title=title,
        publisher=publisher,
        description=description,
        image_url=image_url,
        episodes=episodes,
    )


# ---------------------------------------------------------------------------
# Episode-matching for spotify_episode
# ---------------------------------------------------------------------------


def _pick_episode_by_title(
    episodes: list[PodcastEpisode], target_title: str
) -> PodcastEpisode | None:
    if not episodes:
        return None
    target = _normalize(target_title)
    if not target:
        return None
    # 1. Exact (case-insensitive) title match.
    for ep in episodes:
        if _normalize(ep.title) == target:
            return ep
    # 2. Substring match (target inside episode title or vice versa). If
    #    multiple, prefer the most recent (RSS is reverse-chronological so
    #    that's the earliest in our list).
    matches = [
        ep for ep in episodes
        if target in _normalize(ep.title) or _normalize(ep.title) in target
    ]
    if matches:
        # Prefer the one with the latest pub_date if parseable.
        def _pub_key(ep: PodcastEpisode) -> tuple[int, str]:
            if not ep.pub_date:
                return (0, "")
            try:
                datetime.fromisoformat(ep.pub_date.replace("Z", "+00:00"))
                return (1, ep.pub_date)
            except Exception:
                return (0, ep.pub_date or "")
        matches.sort(key=_pub_key, reverse=True)
        return matches[0]
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def preview_podcast(url: str) -> PodcastPreview:
    kind = _classify(url)

    if kind == "spotify_show":
        meta = _scrape_spotify(url)
        # The Spotify show embed reliably exposes the show name via
        # entity.subtitle (the entity itself is the latest episode of the
        # show). entity_name is therefore an episode title here, not the show
        # name -- prefer subtitle, fall back to entity_name only as last resort.
        show_name = meta.get("show_name") or meta.get("entity_name")
        if not show_name:
            raise PodcastError(
                "could not extract show name from Spotify page "
                "- try pasting the RSS URL directly"
            )
        # For shows, `entity_name` is the latest episode title -- use it as a
        # disambiguation fallback when publisher is missing.
        latest_ep = meta.get("entity_name") if meta.get("entity_type") == "episode" else None
        rss_url, itunes_rec = _resolve_rss_via_itunes(
            show_name,
            publisher=meta.get("publisher"),
            episode_title=latest_ep,
        )
        rss_bytes = _http_get(rss_url, accept="application/rss+xml, application/xml, text/xml")
        preview = _parse_rss(rss_bytes, rss_url=rss_url)
        preview.source = "spotify_show"
        preview.image_url = (
            meta.get("cover_url")
            or preview.image_url
            or itunes_rec.get("artworkUrl600")
            or itunes_rec.get("artworkUrl100")
        )
        # Prefer the publisher we scraped from Spotify (it's the disambiguating
        # source of truth) over RSS itunes:author, which can be a generic
        # placeholder like "Buzzsprout user".
        preview.publisher = (
            meta.get("publisher")
            or preview.publisher
            or itunes_rec.get("artistName")
        )
        return preview

    if kind == "spotify_episode":
        meta = _scrape_spotify(url)
        ep_title = meta.get("entity_name")
        if not ep_title:
            raise PodcastError(
                "could not extract episode title from Spotify page"
            )
        show_name = meta.get("show_name")
        if not show_name:
            raise PodcastError(
                "could not determine the show this episode belongs to "
                "- try pasting the show URL or the RSS URL"
            )
        rss_url, itunes_rec = _resolve_rss_via_itunes(
            show_name,
            publisher=meta.get("publisher"),
            episode_title=ep_title,
        )
        rss_bytes = _http_get(rss_url, accept="application/rss+xml, application/xml, text/xml")
        preview = _parse_rss(rss_bytes, rss_url=rss_url)
        match = _pick_episode_by_title(preview.episodes, ep_title)
        if match is None:
            raise PodcastError(
                f"could not find episode '{ep_title}' in RSS for show '{show_name}' "
                "- the feed may not include this episode yet"
            )
        return PodcastPreview(
            source="spotify_episode",
            rss_url=rss_url,
            title=preview.title,
            publisher=meta.get("publisher") or preview.publisher or itunes_rec.get("artistName"),
            description=preview.description,
            image_url=meta.get("cover_url") or preview.image_url
                or itunes_rec.get("artworkUrl600")
                or itunes_rec.get("artworkUrl100"),
            episodes=[match],
        )

    if kind == "rss":
        rss_bytes = _http_get(url, accept="application/rss+xml, application/xml, text/xml")
        return _parse_rss(rss_bytes, rss_url=url)

    if kind == "direct_audio":
        return _direct_audio_preview(url)

    # Last-ditch: HEAD-probe for content type. application/rss+xml -> RSS,
    # audio/* -> direct.
    ct = _http_head_content_type(url)
    if ct:
        if "rss" in ct or "xml" in ct:
            rss_bytes = _http_get(url, accept="application/rss+xml, application/xml, text/xml")
            return _parse_rss(rss_bytes, rss_url=url)
        if ct.startswith("audio/"):
            return _direct_audio_preview(url)

    raise PodcastError(
        "unrecognized URL - expected a Spotify show/episode link, an RSS "
        "feed URL, or a direct audio file URL"
    )


def _direct_audio_preview(url: str) -> PodcastPreview:
    # The mp3 URL is handed straight to the bulk-ingest path, which fetches
    # it server-side via yt-dlp's generic extractor. Apply the SSRF gate
    # here so a user can't smuggle file:// or an internal IP in.
    _validate_url(url)
    # Use the URL filename as the title.
    path = urllib.parse.urlsplit(url).path
    filename = path.rsplit("/", 1)[-1] or url
    # Strip trailing extension for a cleaner title.
    title = re.sub(r"\.(mp3|m4a|aac|wav)$", "", filename, flags=re.IGNORECASE)
    if not title:
        title = filename
    ep = PodcastEpisode(
        guid="mp3-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
        title=title or "(untitled audio)",
        description=None,
        pub_date=None,
        duration_sec=None,
        mp3_url=url,
        image_url=None,
    )
    return PodcastPreview(
        source="direct_audio",
        rss_url=None,
        title=title or None,
        publisher=None,
        description=None,
        image_url=None,
        episodes=[ep],
    )
