#!/usr/bin/env python3
"""Subscribe to YouTube channels and auto-queue new uploads.

Reads:  feeds.txt — one channel URL per line (any of: channel/UCxxx, @handle, playlist)
Writes: queue.txt — appends new video URLs not already in queue or library
Tracks: scripts/_feed_state.json — last-seen video IDs per feed

Usage:
  python3 scripts/ingest_feeds.py            # check feeds, append new videos to queue
  python3 scripts/ingest_feeds.py --dry      # show what would be queued, no writes

Requires: yt-dlp (brew install yt-dlp)

Pair with the schedule skill to run weekly:
  /schedule create "podcast-insights: check feeds for new uploads" weekly
"""
import json, subprocess, sys, re
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parent.parent
FEEDS = ROOT / "feeds.txt"
QUEUE = ROOT / "queue.txt"
VIDEOS = ROOT / "videos"
STATE = ROOT / "scripts" / "_feed_state.json"

def known_video_ids():
    ids = set()
    if VIDEOS.exists():
        ids |= {p.name for p in VIDEOS.iterdir() if p.is_dir() and not p.is_symlink()}
    if QUEUE.exists():
        for line in QUEUE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", line)
            if m:
                ids.add(m.group(1))
    return ids

def fetch_latest(feed_url, n=10):
    """Use yt-dlp to list latest video IDs from a channel/playlist."""
    cmd = ["yt-dlp", "--flat-playlist", "--print", "%(id)s|||%(title)s|||%(upload_date)s", "--playlist-end", str(n), feed_url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
    except subprocess.CalledProcessError as e:
        print(f"  ! yt-dlp failed for {feed_url}: {e.stderr.strip()[:200]}")
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("|||")
        if len(parts) >= 1 and parts[0] and len(parts[0]) == 11:
            out.append({"id": parts[0], "title": parts[1] if len(parts) > 1 else "", "upload_date": parts[2] if len(parts) > 2 else ""})
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--per-feed", type=int, default=5, help="latest N videos to consider per feed")
    args = ap.parse_args()

    if not FEEDS.exists():
        print(f"No {FEEDS.name}. Create it with one feed URL per line.")
        print("Example feeds:")
        print("  https://www.youtube.com/@allin")
        print("  https://www.youtube.com/@DwarkeshPatel")
        sys.exit(0)

    feeds = [l.strip() for l in FEEDS.read_text().splitlines() if l.strip() and not l.strip().startswith("#")]
    if not feeds:
        print("feeds.txt is empty.")
        sys.exit(0)

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    known = known_video_ids()
    new_lines = []

    for feed in feeds:
        print(f"Checking {feed}...")
        seen_before = set(state.get(feed, []))
        latest = fetch_latest(feed, n=args.per_feed)
        for v in latest:
            if v["id"] in known or v["id"] in seen_before:
                continue
            url = f"https://www.youtube.com/watch?v={v['id']}"
            comment = f"# {v.get('title','')} ({v.get('upload_date','')})"
            new_lines.append(comment)
            new_lines.append(url)
            seen_before.add(v["id"])
            print(f"  + {v.get('title','')[:70]}")
        state[feed] = list(seen_before)

    if not new_lines:
        print("\nNothing new.")
        return

    if args.dry:
        print(f"\n[dry] would append {len(new_lines)//2} videos to {QUEUE.name}")
        return

    block = "\n\n# auto-ingested " + str(__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")) + "\n" + "\n".join(new_lines) + "\n"
    with QUEUE.open("a") as f:
        f.write(block)
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))
    print(f"\nAppended {len(new_lines)//2} new videos to {QUEUE.name}. Run process the queue to fetch them.")

if __name__ == "__main__":
    main()
