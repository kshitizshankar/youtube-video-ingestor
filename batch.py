"""Batch-transcribe YouTube videos by scanning a markdown file (or a URL list).

Examples
--------
  uv run python batch.py --from-markdown path/to/README.md
  uv run python batch.py --urls urls.txt --diarize
  uv run python batch.py https://youtu.be/abc https://youtu.be/def --diarize

Skips any URL whose <video_id>.json already exists in --out (default ./output).
Shells out to ingest.py per URL — model loads once per URL, but the dedup +
skip logic means re-runs are cheap.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path


YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)"
    r"([A-Za-z0-9_-]{6,})"
)


def extract_video_ids(text: str) -> list[str]:
    seen, out = set(), []
    for m in YOUTUBE_RE.finditer(text):
        vid = m.group(1)
        if vid not in seen:
            seen.add(vid)
            out.append(vid)
    return out


def url_for(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def collect_urls(args: argparse.Namespace) -> list[str]:
    urls: list[str] = []
    if args.from_markdown:
        text = Path(args.from_markdown).read_text(encoding="utf-8", errors="replace")
        urls.extend(url_for(v) for v in extract_video_ids(text))
    if args.urls:
        for line in Path(args.urls).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    urls.extend(args.url)
    # Dedupe by video id while preserving order
    seen, deduped = set(), []
    for u in urls:
        m = YOUTUBE_RE.search(u)
        key = m.group(1) if m else u
        if key not in seen:
            seen.add(key)
            deduped.append(u)
    return deduped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", nargs="*", help="One or more YouTube URLs")
    ap.add_argument("--from-markdown", help="Path to a markdown file to scan for YouTube URLs")
    ap.add_argument("--urls", help="Path to a text file with one URL per line (# = comment)")
    ap.add_argument("--out", default="./output", help="Output directory (default ./output)")
    ap.add_argument("--ingest-script", default="ingest.py")
    # Anything after -- is forwarded as-is to ingest.py
    ap.add_argument("--diarize", action="store_true",
                    help="Pass --diarize through to ingest.py")
    ap.add_argument("--model", help="Pass --model X through to ingest.py")
    ap.add_argument("--no-batched", action="store_true",
                    help="Pass --no-batched through to ingest.py")
    args = ap.parse_args()

    urls = collect_urls(args)
    if not urls:
        print("No URLs found. Pass URLs positionally, --urls FILE, or --from-markdown FILE.",
              file=sys.stderr)
        sys.exit(2)

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(urls)} unique URL(s):")
    for i, u in enumerate(urls, 1):
        print(f"  {i:2}. {u}")
    print()

    succeeded, skipped, failed = [], [], []
    overall_start = time.time()

    for i, url in enumerate(urls, 1):
        m = YOUTUBE_RE.search(url)
        vid = m.group(1) if m else None
        json_path = out_dir / f"{vid}.json" if vid else None

        print(f"\n=== [{i}/{len(urls)}] {url} ===")
        if json_path and json_path.exists():
            print(f"    SKIP — {json_path.name} already exists")
            skipped.append(url)
            continue

        cmd = ["uv", "run", "python", args.ingest_script, url, "--out", str(out_dir)]
        if args.diarize:
            cmd.append("--diarize")
        if args.model:
            cmd.extend(["--model", args.model])
        if args.no_batched:
            cmd.append("--no-batched")

        t0 = time.time()
        result = subprocess.run(cmd)
        elapsed = time.time() - t0

        if result.returncode == 0:
            succeeded.append((url, elapsed))
            print(f"    OK in {elapsed:.1f}s")
        else:
            failed.append((url, result.returncode))
            print(f"    FAILED (exit {result.returncode}) after {elapsed:.1f}s")

    total = time.time() - overall_start
    print("\n" + "=" * 60)
    print(f"BATCH SUMMARY ({total / 60:.1f} min total)")
    print(f"  ok:      {len(succeeded)}")
    print(f"  skipped: {len(skipped)}")
    print(f"  failed:  {len(failed)}")
    if failed:
        print("\nFailures:")
        for url, rc in failed:
            print(f"  exit {rc}  {url}")
        sys.exit(1)


if __name__ == "__main__":
    main()
