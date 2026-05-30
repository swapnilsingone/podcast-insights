#!/usr/bin/env python3
"""One-shot: assign video-level `topics` to already-archived videos.

Cheap path — sends only each video's title + thesis (not transcripts) to Claude
in a SINGLE call, gets back a {video_id: [topics]} map, and writes `topics` into
each videos/<id>/insights.json and analysis.json. Then run build_indexes.py.

Controlled vocabulary (must match scripts/analyze.py):
  ai, finance, business, macro, tech, science, crypto

Usage:
  python3 scripts/backfill_topics.py            # tag videos missing topics
  python3 scripts/backfill_topics.py --all      # re-tag every video
  python3 scripts/backfill_topics.py --dry-run  # print map, write nothing
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEOS = ROOT / "videos"
VOCAB = {"ai", "finance", "business", "macro", "tech", "science", "crypto"}

SYSTEM = ("You are a precise librarian. You assign each video 1-3 topics from a "
          "fixed vocabulary, most important first. Output ONLY JSON.")

PROMPT_TMPL = """Assign topics to each video below. Use ONLY this vocabulary:
- ai: AI/ML capability, research, models
- finance: markets, stocks, valuations, investing, earnings, selloffs, market caps
- business: company strategy, deals, org moves
- macro: economy, policy, geopolitics, regulation
- tech: non-AI technology, hardware, infrastructure
- science: research outside AI
- crypto: crypto/web3

Rules: 1-3 topics per video, MOST important first. Use `finance` when the episode
dwells on stock prices, market caps, selloffs, or investment theses. Only tag what
the episode genuinely centers on.

Videos:
{videos}

Output a SINGLE JSON object mapping video_id -> array of topics, e.g.:
{{"abc123def45": ["ai", "finance"]}}
Output ONLY the JSON object. No prose, no backticks."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="re-tag every video, not just untagged")
    ap.add_argument("--dry-run", action="store_true", help="print the map, write nothing")
    ap.add_argument("--model", default=os.environ.get("CLAUDE_MODEL", "claude-opus-4-8"))
    args = ap.parse_args()

    targets = []
    for vd in sorted(d for d in VIDEOS.iterdir() if d.is_dir() and not d.is_symlink()):
        ins_path = vd / "insights.json"
        if not ins_path.exists():
            continue
        ins = json.loads(ins_path.read_text())
        if not args.all and ins.get("topics"):
            continue
        v = ins.get("video", {})
        targets.append({
            "video_id": vd.name,
            "title": v.get("title", ""),
            "thesis": ins.get("thesis", ""),
        })

    if not targets:
        print("Nothing to tag — all videos already have topics (use --all to re-tag).")
        return 0

    listing = "\n".join(
        f'- {t["video_id"]}: "{t["title"]}" — {t["thesis"]}' for t in targets
    )
    prompt = PROMPT_TMPL.format(videos=listing)

    print(f"Tagging {len(targets)} video(s) via {args.model} (title+thesis only)…")
    res = subprocess.run(
        ["claude", "-p", "--output-format", "text",
         "--append-system-prompt", SYSTEM, "--model", args.model],
        input=prompt, capture_output=True, text=True, timeout=300,
    )
    if res.returncode != 0:
        print(f"claude failed (exit {res.returncode}): {res.stderr[:500]}", file=sys.stderr)
        return res.returncode

    raw = res.stdout.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw).strip()
    s, e = raw.find('{'), raw.rfind('}')
    if s == -1 or e == -1:
        print(f"No JSON in output:\n{raw[:500]}", file=sys.stderr)
        return 3
    mapping = json.loads(raw[s:e + 1])

    # Validate against vocab; keep order, drop unknowns, cap at 3.
    clean = {}
    for vid, topics in mapping.items():
        kept = [t for t in (topics or []) if t in VOCAB][:3]
        clean[vid] = kept

    print("\nTopic assignments:")
    for t in targets:
        vid = t["video_id"]
        print(f"  {vid}  {clean.get(vid, [])}   {t['title'][:60]}")

    missing = [t["video_id"] for t in targets if not clean.get(t["video_id"])]
    if missing:
        print(f"\nWARN: no valid topics for: {missing}", file=sys.stderr)

    if args.dry_run:
        print("\n--dry-run: no files written.")
        return 0

    written = 0
    for vid, topics in clean.items():
        if not topics:
            continue
        vd = VIDEOS / vid
        for fname in ("insights.json", "analysis.json"):
            p = vd / fname
            if not p.exists():
                continue
            data = json.loads(p.read_text())
            data["topics"] = topics
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            written += 1
    print(f"\nWrote topics into {written} file(s). Now run: python3 scripts/build_indexes.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
