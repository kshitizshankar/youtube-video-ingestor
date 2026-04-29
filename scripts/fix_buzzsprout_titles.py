"""One-shot reconciliation for Buzzsprout-ingested podcast rows whose
metadata extraction stored a 28-char yt-dlp `info["id"]` slug as the title
and recorded `source='youtube'`.

For each broken row:
  * derive a human title from the URL slug
  * set source='podcast', show_name='The Moonshot Podcast' (when the URL
    points at the known Moonshot show on Buzzsprout)
  * set channel to the host name
  * fetch the per-episode cover art from the Buzzsprout RSS feed and set
    image_url
  * write back to both transcript.json AND the videos row

Idempotent — running twice is a no-op for already-fixed rows.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
DB_PATH = OUTPUT_DIR / "app.db"

# Buzzsprout show id → (show_name, channel/host, show_url) lookup. Extend
# as more Moonshot-style shows surface.
KNOWN_SHOWS: dict[str, tuple[str, str, str]] = {
    "2406640": (
        "The Moonshot Podcast",
        "Tatjana Pandurevic",
        "https://www.buzzsprout.com/2406640",
    ),
}

# Words that stay lowercase in title case (unless they're the first word).
SMALL_WORDS = {
    "a", "an", "the",
    "and", "or", "but", "nor", "so", "yet",
    "as", "at", "by", "for", "in", "of", "on", "to", "from", "into",
    "with", "without", "via", "vs",
    "is", "are", "be",
}

# Acronyms / brands we want fully uppercased after the naive title-case.
UPPERCASE_TOKENS = {
    "ai", "agi", "ml", "llm", "api", "saas", "ux", "ui",
    "ceo", "cto", "cfo", "coo", "cmo", "cio",
    "ipo", "vc", "ar", "vr", "xr",
    "aws", "gcp", "gpu", "cpu", "ssd",
    "nfl", "nba", "mlb", "nbl",
    "uk", "usa", "eu",
    "b2b", "b2c", "iot",
    "rag", "ocr", "tts", "stt",
}


def _titleify_token(tok: str, is_first: bool) -> str:
    """Title-case a single slug token with small-word + acronym rules."""
    low = tok.lower()
    if low in UPPERCASE_TOKENS:
        return low.upper()
    # "$500M", "100b", "70m" — if the token starts with a digit, keep digits
    # then uppercase any trailing letters (m, b, k, x).
    if re.match(r"^\d", tok):
        m = re.match(r"^(\$?\d[\d,.]*)([a-zA-Z]*)$", tok)
        if m:
            return m.group(1) + m.group(2).upper()
    # Small words stay lowercase except as the first word.
    if low in SMALL_WORDS and not is_first:
        return low
    # Default: capitalize first letter, lower the rest.
    if low.endswith("'s"):
        return low[:-2].capitalize() + "'s"
    return low.capitalize()


def title_from_slug(slug: str) -> str:
    """`how-lendi-shipped-a-customer-facing-ai-agent-in-16-weeks-david-hyman`
    → `How Lendi Shipped a Customer Facing AI Agent in 16 Weeks David Hyman`
    """
    # Buzzsprout slugs encode apostrophes as `-s-` between words. Restore
    # the apostrophe so "world-s-largest" → "world's largest".
    slug = re.sub(r"(?<=[a-z])-s-(?=[a-z])", "'s ", slug)
    # Split on dashes AND whitespace so the post-apostrophe words tokenize
    # cleanly (otherwise "extag's chief" becomes one merged token and the
    # second word gets stuck lowercase).
    parts = [p for p in re.split(r"[\s-]+", slug) if p]
    out: list[str] = []
    for i, p in enumerate(parts):
        out.append(_titleify_token(p, i == 0))
    return " ".join(out)


_RSS_NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "media": "http://search.yahoo.com/mrss/",
}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def fetch_rss_image_map(show_id: str) -> dict[str, str]:
    """Fetch the Buzzsprout RSS for `show_id` and return a map from
    enclosure mp3 URL → episode cover art URL. Falls back to the channel
    cover when an episode has no <itunes:image>."""
    url = f"https://feeds.buzzsprout.com/{show_id}.rss"
    print(f"  fetching RSS: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
    root = ET.fromstring(body)
    channel = root.find("channel")
    if channel is None:
        return {}
    # Channel-level fallback image.
    channel_img = None
    img_el = channel.find("itunes:image", _RSS_NS)
    if img_el is not None:
        channel_img = img_el.get("href")
    out: dict[str, str] = {}
    for item in channel.findall("item"):
        enc = item.find("enclosure")
        mp3 = enc.get("url") if enc is not None else None
        if not mp3:
            continue
        ep_img_el = item.find("itunes:image", _RSS_NS)
        ep_img = ep_img_el.get("href") if ep_img_el is not None else None
        if not ep_img:
            mc = item.find("media:content", _RSS_NS)
            ep_img = mc.get("url") if mc is not None else None
        out[mp3.strip()] = ep_img or channel_img or ""
    return {k: v for k, v in out.items() if v}


def parse_buzzsprout(url: str) -> tuple[str | None, str | None]:
    """Return (show_id, slug_for_title) if the URL is a Buzzsprout episode mp3.
    Otherwise (None, None)."""
    try:
        u = urlparse(url)
    except Exception:
        return None, None
    if "buzzsprout.com" not in (u.netloc or ""):
        return None, None
    # Path shape: /<show_id>/episodes/<episode_id>-<slug>.mp3
    m = re.match(r"^/(\d+)/episodes/(\d+)-(.+?)\.mp3$", u.path)
    if not m:
        return None, None
    return m.group(1), m.group(3)


def is_opaque(title: str | None, video_id: str) -> bool:
    if not title:
        return True
    t = title.strip()
    if not t:
        return True
    if t == video_id:
        return True
    if re.match(r"^[a-zA-Z0-9_-]{10,}$", t) and " " not in t:
        return True
    return False


def _load_disk(video_id: str) -> tuple[Path, dict]:
    p = OUTPUT_DIR / video_id / "transcript.json"
    if not p.exists():
        return p, {}
    try:
        return p, json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return p, {}


def main(dry_run: bool) -> int:
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}", file=sys.stderr)
        return 1
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, title, source, channel, url, image_url, show_name, show_url "
        "FROM videos WHERE archived=0"
    ).fetchall()

    rss_image_cache: dict[str, dict[str, str]] = {}

    def get_episode_image(show_id: str, mp3_url: str) -> str | None:
        if show_id not in rss_image_cache:
            try:
                rss_image_cache[show_id] = fetch_rss_image_map(show_id)
            except Exception as e:
                print(f"  RSS fetch failed for show {show_id}: {e}")
                rss_image_cache[show_id] = {}
        return rss_image_cache[show_id].get(mp3_url)

    proposals: list[dict] = []
    for r in rows:
        # Prefer the URL stored in transcript.json — it's the original ingest
        # URL and survives any later DB writes.
        tj_path, disk = _load_disk(r["id"])
        url = disk.get("url") or r["url"] or ""
        show_id, slug = parse_buzzsprout(url)
        broken = is_opaque(r["title"], r["id"])
        needs_image = (
            (r["source"] == "podcast" or broken)
            and not r["image_url"]
            and show_id is not None
        )
        if not broken and not needs_image:
            continue
        if not show_id and broken:
            proposals.append({"id": r["id"], "skip": True, "reason": "non-buzzsprout url"})
            continue

        change: dict = {
            "id": r["id"],
            "skip": False,
            "tj_path": str(tj_path),
            "old_disk": disk,
            "fields": {},
        }
        if broken:
            show_name, channel, show_url = KNOWN_SHOWS.get(
                show_id,
                (None, None, f"https://www.buzzsprout.com/{show_id}"),
            )
            change["fields"]["title"] = (r["title"], title_from_slug(slug))
            change["fields"]["source"] = (r["source"], "podcast")
            if not r["channel"] and channel:
                change["fields"]["channel"] = (r["channel"], channel)
            if not r["show_name"] and show_name:
                change["fields"]["show_name"] = (r["show_name"], show_name)
            if not r["show_url"] and show_url:
                change["fields"]["show_url"] = (r["show_url"], show_url)
        if needs_image and show_id:
            ep_image = get_episode_image(show_id, url)
            if ep_image:
                change["fields"]["image_url"] = (r["image_url"], ep_image)
        if not change["fields"]:
            continue
        proposals.append(change)

    actionable = [p for p in proposals if not p["skip"] and p.get("fields")]
    print(f"\nfound {len(proposals)} candidate rows. proposed changes:\n")
    for p in proposals:
        if p["skip"]:
            print(f"  - {p['id']}  SKIP ({p['reason']})")
            continue
        print(f"  - {p['id']}")
        for k, (old, new) in p["fields"].items():
            new_disp = new if not (isinstance(new, str) and len(new) > 80) else new[:77] + "..."
            print(f"      {k}: {old!r} -> {new_disp!r}")

    if dry_run:
        print(f"\n(dry run) would update {len(actionable)} rows. pass --apply to write.\n")
        con.close()
        return 0

    if not actionable:
        print("\nnothing to do.\n")
        con.close()
        return 0

    print(f"\napplying changes to {len(actionable)} rows...")
    for p in actionable:
        # Build a flat dict of the new values for both transcript.json + DB.
        new_vals = {k: v for k, (_, v) in p["fields"].items()}

        # 1) Patch transcript.json on disk.
        new_disk = dict(p["old_disk"])
        for k, v in new_vals.items():
            new_disk[k] = v
        Path(p["tj_path"]).write_text(
            json.dumps(new_disk, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # 2) Patch the videos row dynamically — only update columns whose
        #    fields actually changed. Allowlisted to schema columns to
        #    avoid SQL injection from a manipulated `fields` dict.
        SQL_COLS = {
            "title", "source", "channel", "show_name", "show_url", "image_url",
        }
        cols = [k for k in new_vals.keys() if k in SQL_COLS]
        if cols:
            set_clause = ", ".join(f"{c}=?" for c in cols) + ", updated_at=datetime('now')"
            params = [new_vals[c] for c in cols] + [p["id"]]
            con.execute(f"UPDATE videos SET {set_clause} WHERE id=?", params)
        summary = ", ".join(f"{k}={v!r}" if k != "image_url" else "image_url=set"
                            for k, v in new_vals.items())
        print(f"  ok  {p['id']}  -> {summary[:120]}")

    con.commit()
    con.close()
    print("\ndone.\n")
    return 0


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    sys.exit(main(dry_run=not apply))
