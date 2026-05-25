#!/usr/bin/env python3
"""Run Claude CLI on a transcribed video to produce structured insight data.

Inputs (under videos/<id>/source/):
  - <id>.info.json   (from yt-dlp)
  - transcript.txt   (from scripts/transcribe.py)

Outputs (under videos/<id>/):
  - analysis.json    (single combined Claude output, kept for debugging)

Usage:
  python3 scripts/analyze.py <video_id> [--model claude-opus-4-7|claude-sonnet-4-6]

The Claude model is controlled by CLAUDE_MODEL env var or --model flag, defaulting
to whatever the local claude CLI uses. Sonnet 4.6 is ~5x cheaper than Opus 4.7
and good enough for most transcripts; Opus extracts subtler insights.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SYSTEM_PROMPT = """You are a research analyst extracting *non-obvious* insights from a podcast or video transcript. Output a single valid JSON object — no markdown fences, no commentary, no preamble. Just the JSON."""

USER_PROMPT_TEMPLATE = """Below is a YouTube transcript with timestamps. Extract structured insights.

# Source metadata
- video_id: {video_id}
- title: {title}
- channel: {channel}
- duration_seconds: {duration}
- upload_date: {upload_date}
- url: {url}
- thumbnail: {thumbnail}

# Chapters (from YouTube)
{chapters_json}

# Transcript
{transcript}

# Your task

Produce a SINGLE JSON object with this exact shape:

{{
  "video": {{
    "id": "{video_id}",
    "title": "...",
    "channel": "...",
    "duration_seconds": {duration},
    "thumbnail": "...",
    "url": "...",
    "upload_date": "{upload_date}",
    "display_title_html": "..."
  }},
  "thesis": "One sentence — what is this video actually arguing or teaching? Specific, not generic.",
  "chapters": [
    {{ "start": 0, "title": "..." }}
  ],
  "insights": [
    {{
      "id": 1,
      "category": "story|framework|data-point|counterintuitive|mental-model|prediction|actionable|definition",
      "headline": "≤10 words, specific, non-obvious",
      "body": "1-3 sentences. Names, numbers, mechanisms. No filler.",
      "start_seconds": 123,
      "end_seconds": 145
    }}
  ],
  "quotes": [
    {{ "text": "verbatim line", "speaker": "Name or 'show hosts'", "start_seconds": 0 }}
  ],
  "resources": [
    {{ "name": "Person/work/company", "context": "what they were cited for" }}
  ],
  "action_items": [
    "If you take one thing from this conversation, do X because Y."
  ],
  "stats": [
    {{ "label": "Short label", "val": "$45B", "unit": "/ 3y", "sub": "1 sentence of context" }}
  ],
  "entities": {{
    "people": [{{ "name": "...", "role": "...", "speaker": false, "mentions": 1, "first_seconds": 0, "context": "..." }}],
    "companies": [{{ "name": "...", "mentions": 1, "first_seconds": 0, "context": "..." }}],
    "products_tech": [{{ "name": "...", "type": "...", "first_seconds": 0, "context": "..." }}],
    "frameworks_concepts": [{{ "name": "...", "first_seconds": 0, "summary": "..." }}],
    "books_papers": [{{ "name": "...", "author": "...", "context": "..." }}]
  }},
  "predictions": [
    {{
      "id": "{video_id}-p1",
      "claim": "The actual prediction",
      "speaker": "Who said it",
      "category": "ai|business|macro|tech|...",
      "start_seconds": 0,
      "stated_at": "{upload_date}",
      "horizon_label": "1-2 years | by 2030 | indefinite",
      "revisit_dates": ["YYYY-MM-DD"],
      "status": "open",
      "notes": ""
    }}
  ]
}}

# Constraints
- 8-14 insights. Skip filler. Every insight cites a timestamp range from the transcript — never fabricate.
- Categories MUST be from the enum listed above.
- `display_title_html` is the editorial-styled title, optionally with one `<em>...</em>` around a key phrase for visual emphasis. Fall back to the plain title if there's no obvious emphasis.
- `chapters` — use the YouTube chapters above if present, otherwise infer 4-8 natural breaks from topic transitions in the transcript.
- 3-6 quotes. Pick punchy, non-obvious lines.
- 4 stats — the four most-citable numbers from the conversation.
- Predictions: every prediction sets `stated_at` to the video upload_date AND gets at least one `revisit_dates` ISO date offset from `stated_at` per `horizon_label`.
- Entities: 5-25 people, 5-25 companies, plus shorter lists for products/concepts/books.

Output ONLY the JSON object. No backticks, no prose."""


def fmt_chapters(chapters):
    if not chapters:
        return "(none)"
    return "\n".join(f"- {c.get('start_time',0):.0f}s: {c.get('title','?')}" for c in chapters)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--model", help="claude model id (e.g. claude-sonnet-4-6, claude-opus-4-7)")
    ap.add_argument("--force", action="store_true", help="re-analyze even if analysis.json exists")
    args = ap.parse_args()

    vdir = ROOT / "videos" / args.video_id
    out = vdir / "analysis.json"
    if out.exists() and not args.force:
        print(f"  {out} already exists (use --force to overwrite)")
        return 0

    info_path = vdir / "source" / f"{args.video_id}.info.json"
    transcript_path = vdir / "source" / "transcript.txt"
    if not info_path.exists():
        print(f"  ERROR: {info_path} missing — run yt-dlp first", file=sys.stderr)
        return 2
    if not transcript_path.exists():
        print(f"  ERROR: {transcript_path} missing — run scripts/transcribe.py first", file=sys.stderr)
        return 2

    info = json.loads(info_path.read_text())
    transcript = transcript_path.read_text()

    upload_raw = info.get("upload_date", "")
    upload_date = f"{upload_raw[:4]}-{upload_raw[4:6]}-{upload_raw[6:8]}" if len(upload_raw) == 8 else upload_raw

    prompt = USER_PROMPT_TEMPLATE.format(
        video_id=args.video_id,
        title=info.get("title", "").replace('"', "'"),
        channel=info.get("channel") or info.get("uploader") or "Unknown",
        duration=info.get("duration", 0),
        upload_date=upload_date,
        url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={args.video_id}",
        thumbnail=info.get("thumbnail", f"https://i.ytimg.com/vi/{args.video_id}/maxresdefault.jpg"),
        chapters_json=fmt_chapters(info.get("chapters", [])),
        transcript=transcript,
    )

    model = args.model or os.environ.get("CLAUDE_MODEL")
    cmd = ["claude", "-p", prompt, "--output-format", "text",
           "--append-system-prompt", SYSTEM_PROMPT]
    if model:
        cmd.extend(["--model", model])

    print(f"  Invoking claude ({'model='+model if model else 'default model'}, transcript {len(transcript):,} chars)…")
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if res.returncode != 0:
        print(f"  claude failed (exit {res.returncode}): {res.stderr[:500]}", file=sys.stderr)
        return res.returncode

    raw = res.stdout.strip()
    # Strip markdown code fences if Claude added them despite the instruction.
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw).strip()
    # Find the first { and last } in case there's stray preamble.
    start = raw.find('{')
    end = raw.rfind('}')
    if start == -1 or end == -1:
        print(f"  ERROR: no JSON object in output:\n{raw[:500]}", file=sys.stderr)
        return 3
    raw = raw[start:end+1]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  ERROR: claude returned malformed JSON: {e}", file=sys.stderr)
        (vdir / "analysis_raw.txt").write_text(res.stdout)
        print(f"  Raw output saved to {vdir / 'analysis_raw.txt'}", file=sys.stderr)
        return 3

    # Sanity-check required keys.
    required = ["video", "thesis", "insights", "quotes", "entities", "predictions"]
    missing = [k for k in required if k not in data]
    if missing:
        print(f"  ERROR: analysis JSON missing keys: {missing}", file=sys.stderr)
        return 3

    out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"  Wrote {out}")
    print(f"    {len(data.get('insights', []))} insights · "
          f"{len(data.get('quotes', []))} quotes · "
          f"{len(data.get('predictions', []))} predictions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
