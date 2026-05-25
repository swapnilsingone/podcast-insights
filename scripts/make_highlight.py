#!/usr/bin/env python3
"""Build a highlight reel for a processed video.

Usage:
  python3 scripts/make_highlight.py <video_id> [--top N] [--window 18]
  python3 scripts/make_highlight.py gwW8GKwHB3I

Picks the top N insights (default 5), downloads the source video via yt-dlp,
cuts a clip of `window` seconds around each timestamp (starts ~3s before), and
concatenates them into videos/<video_id>/highlight.mp4.

Requires: yt-dlp, ffmpeg. Install with: brew install yt-dlp ffmpeg
"""
import json, sys, os, shutil, subprocess, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEOS = ROOT / "videos"

def need(cmd):
    if not shutil.which(cmd):
        print(f"ERROR: {cmd} not installed. Run: brew install yt-dlp ffmpeg")
        sys.exit(1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--window", type=int, default=18, help="clip duration in seconds")
    ap.add_argument("--pre", type=int, default=3, help="seconds to start before the timestamp")
    ap.add_argument("--quality", default="480", help="max video height (e.g. 360/480/720)")
    args = ap.parse_args()

    need("yt-dlp")
    need("ffmpeg")

    vdir = VIDEOS / args.video_id
    insights_path = vdir / "insights.json"
    if not insights_path.exists():
        print(f"ERROR: {insights_path} not found")
        sys.exit(1)

    data = json.loads(insights_path.read_text())
    url = data["video"]["url"]
    insights = sorted(data["insights"], key=lambda i: i["id"])[: args.top]
    if not insights:
        print("ERROR: no insights to clip")
        sys.exit(1)

    work = vdir / "_highlight_work"
    work.mkdir(exist_ok=True)
    src = work / f"{args.video_id}.mp4"

    if not src.exists():
        print(f"Downloading source video (max {args.quality}p)...")
        subprocess.run([
            "yt-dlp",
            "-f", f"bv*[height<={args.quality}]+ba/b[height<={args.quality}]",
            "--merge-output-format", "mp4",
            "-o", str(src),
            url
        ], check=True)
    else:
        print(f"Reusing existing {src.name}")

    clips = []
    for i, ins in enumerate(insights):
        start = max(0, ins["start_seconds"] - args.pre)
        out = work / f"clip_{i:02d}.mp4"
        print(f"Cutting [{ins['id']:02d}] {ins['headline']}  @ {start}s for {args.window}s")
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", str(start), "-i", str(src), "-t", str(args.window),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            str(out)
        ], check=True)
        clips.append(out)

    concat_list = work / "concat.txt"
    concat_list.write_text("\n".join(f"file '{c.name}'" for c in clips))
    final = vdir / "highlight.mp4"
    print(f"Concatenating into {final.name}...")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy",
        str(final)
    ], check=True)

    # Write a small manifest
    manifest = {
        "video_id": args.video_id,
        "source_url": url,
        "clips": [
            {"id": ins["id"], "headline": ins["headline"], "start_seconds": ins["start_seconds"], "duration": args.window}
            for ins in insights
        ],
        "duration_seconds": args.window * len(insights),
        "path": "highlight.mp4"
    }
    (vdir / "highlight.json").write_text(json.dumps(manifest, indent=2))

    print(f"Done: {final}")
    print(f"Manifest: {vdir / 'highlight.json'}")

if __name__ == "__main__":
    main()
