"""Re-ingest the 19 Moonshot Podcast episodes that crashed before
download (project_videos has the membership row but `videos` does not).

Strategy:
  1. Fetch the Buzzsprout RSS for show 2406640.
  2. Compute the deterministic `aud-<sha1[:12]>` id for each episode's
     mp3_url (matches transcriber._safe_video_id).
  3. Cross-reference with the dangling project_videos rows in the DB.
  4. POST to /api/ingests/podcast with the rich {show, episodes[]} payload
     so the bulk endpoint kicks each ingest with full metadata.

Dry-run by default; pass --apply to actually POST the re-ingest request.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
DB_PATH = OUTPUT_DIR / "app.db"

SHOW_ID = "2406640"
RSS_URL = f"https://feeds.buzzsprout.com/{SHOW_ID}.rss"
SHOW_TITLE = "The Moonshot Podcast"
SHOW_PUBLISHER = "Tatjana Pandurevic"
PROJECT_ID = "the-moonshot-podcast"
SERVER = "http://127.0.0.1:57882"
AUTH = ("vidan", "vidan")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "media": "http://search.yahoo.com/mrss/",
}


def aud_id(mp3_url: str) -> str:
    return "aud-" + hashlib.sha1(mp3_url.encode("utf-8")).hexdigest()[:12]


def parse_iso_pubdate(s: str) -> str | None:
    """Buzzsprout uses RFC 2822-ish dates like 'Tue, 28 Jan 2026 ...'.
    Convert to ISO-8601 so persist_video_to_db gets a clean upload_date."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip()[:25], "%a, %d %b %Y %H:%M:%S").isoformat()
    except Exception:
        return s


def parse_duration(s: str | None) -> float | None:
    """itunes:duration may be 'HH:MM:SS', 'MM:SS', or seconds."""
    if not s:
        return None
    s = s.strip()
    if s.isdigit():
        return float(s)
    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return float(nums[0] * 3600 + nums[1] * 60 + nums[2])
    if len(nums) == 2:
        return float(nums[0] * 60 + nums[1])
    return None


def fetch_rss() -> list[dict]:
    req = urllib.request.Request(RSS_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
    root = ET.fromstring(body)
    channel = root.find("channel")
    channel_img = None
    cimg = channel.find("itunes:image", NS) if channel is not None else None
    if cimg is not None:
        channel_img = cimg.get("href")
    eps: list[dict] = []
    for item in channel.findall("item"):
        enc = item.find("enclosure")
        mp3 = enc.get("url") if enc is not None else None
        if not mp3:
            continue
        title = (item.findtext("title") or "").strip()
        ep_img_el = item.find("itunes:image", NS)
        ep_img = ep_img_el.get("href") if ep_img_el is not None else channel_img
        dur = parse_duration(item.findtext("itunes:duration", default=None, namespaces=NS))
        pub = parse_iso_pubdate(item.findtext("pubDate") or "")
        # Buzzsprout puts a long description in <description>; itunes:summary
        # is sometimes shorter. Prefer description.
        desc = item.findtext("description") or item.findtext("itunes:summary", default=None, namespaces=NS)
        guid = (item.findtext("guid") or "").strip()
        eps.append({
            "guid": guid,
            "title": title,
            "description": desc,
            "pub_date": pub,
            "duration_sec": dur,
            "mp3_url": mp3.strip(),
            "image_url": ep_img,
        })
    return eps


def find_dangling_ids() -> set[str]:
    """Locate videos that should be in the moonshot project but have
    no on-disk transcript.

    Pre-folder-migration this script queried `project_videos` (the m:m
    join table). After migration 005 dropped that table the m:m schema
    is gone and a video's home is `videos.project_id`. Dangling now
    means: project_id matches but the on-disk path no longer has a
    transcript.json (the original failure mode that motivated this
    script -- ingest crashed pre-write). Falls back to project_videos
    on installations that haven't run the folder migration yet."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    has_pv = bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_videos'"
    ).fetchone())
    if has_pv:
        rows = con.execute(
            """
            SELECT pv.video_id
            FROM project_videos pv
            LEFT JOIN videos v ON v.id = pv.video_id
            WHERE pv.project_id = ? AND v.id IS NULL
            """,
            (PROJECT_ID,),
        ).fetchall()
        con.close()
        return set(r["video_id"] for r in rows)
    # Post-migration: every video has a row; "dangling" means the on-
    # disk transcript is missing. Walk every video assigned to the
    # project and check disk. Caller wants candidates for re-ingest;
    # videos with present transcripts don't need re-ingest.
    rows = con.execute(
        "SELECT id, path FROM videos WHERE project_id = ? AND archived = 0",
        (PROJECT_ID,),
    ).fetchall()
    con.close()
    out: set[str] = set()
    for r in rows:
        p = r["path"]
        if not p:
            out.add(r["id"])
            continue
        if not (Path(p) / "transcript.json").is_file():
            out.add(r["id"])
    return out


def post_reingest(payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    creds = f"{AUTH[0]}:{AUTH[1]}".encode("ascii")
    import base64
    auth_header = "Basic " + base64.b64encode(creds).decode("ascii")
    req = urllib.request.Request(
        f"{SERVER}/api/ingests/podcast",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": auth_header,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main(apply: bool) -> int:
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}", file=sys.stderr)
        return 1

    dangling = find_dangling_ids()
    print(f"dangling project_videos rows: {len(dangling)}")
    eps = fetch_rss()
    print(f"RSS episodes for show {SHOW_ID}: {len(eps)}")

    # Annotate each episode with its computed aud-id and whether it's
    # dangling.
    matched: list[dict] = []
    for e in eps:
        e["aud_id"] = aud_id(e["mp3_url"])
        if e["aud_id"] in dangling:
            matched.append(e)
    unmatched_dangling = dangling - {e["aud_id"] for e in matched}

    print(f"\nepisodes to re-ingest ({len(matched)}):")
    log_path = Path(__file__).parent / "_reingest_match.log"
    with log_path.open("w", encoding="utf-8") as f:
        for m in matched:
            line = f"  {m['aud_id']}  {m['title']}"
            f.write(line + "\n")
            # Print a safe ASCII summary to stdout (Windows cp1252 can't
            # always render the host's accented name).
            safe = m["title"].encode("ascii", "replace").decode("ascii")
            print(f"  {m['aud_id']}  {safe[:80]}")
    print(f"  (full UTF-8 list written to {log_path})")

    if unmatched_dangling:
        print(f"\nWARNING: {len(unmatched_dangling)} dangling ids did not match RSS:")
        for u in unmatched_dangling:
            print(f"  {u}")

    if not apply:
        print("\n(dry run) pass --apply to POST the bulk re-ingest request.\n")
        return 0

    # Build the bulk-podcast payload. Server will dedup against any rows
    # that already exist, so re-running is safe.
    payload = {
        "project_id": PROJECT_ID,
        "show": {
            "title": SHOW_TITLE,
            "publisher": SHOW_PUBLISHER,
            "image_url": matched[0].get("image_url") if matched else None,
            "rss_url": RSS_URL,
        },
        "episodes": [
            {
                "guid": m["guid"],
                "title": m["title"],
                "description": m["description"],
                "pub_date": m["pub_date"],
                "duration_sec": m["duration_sec"],
                "mp3_url": m["mp3_url"],
                "image_url": m["image_url"],
            }
            for m in matched
        ],
    }
    print(f"\nPOSTing {len(payload['episodes'])} episodes to {SERVER}/api/ingests/podcast ...")
    try:
        resp = post_reingest(payload)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"HTTP {e.code}: {body}")
        return 2
    except Exception as e:
        print(f"POST failed: {type(e).__name__}: {e}")
        return 2

    print(f"\nresponse:")
    print(f"  job_ids:  {len(resp.get('job_ids', []))}")
    for j in resp.get("job_ids", []):
        print(f"    {j}")
    print(f"  skipped:  {len(resp.get('skipped', []))}")
    for s in resp.get("skipped", []):
        print(f"    {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))
