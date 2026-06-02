#!/usr/bin/env python3
"""Render a video's index.html + per-video JSON sidecars from analysis.json.

Input:
  videos/<id>/analysis.json   — single combined Claude output from scripts/analyze.py

Outputs (under videos/<id>/):
  - index.html         — rendered from scripts/templates/video_template.html
  - insights.json      — { video, thesis, chapters, insights, quotes, resources, action_items, stats }
  - entities.json      — { video_id, people, companies, products_tech, frameworks_concepts, books_papers }
  - predictions.json   — { video_id, predictions, validated_predictions }
  - notes.md           — created if missing (never overwritten)

Usage:
  python3 scripts/build_video.py <video_id>
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "scripts" / "templates" / "video_template.html"


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    args = ap.parse_args()

    vdir = ROOT / "videos" / args.video_id
    analysis_path = vdir / "analysis.json"
    if not analysis_path.exists():
        print(f"ERROR: {analysis_path} missing — run scripts/analyze.py first", file=sys.stderr)
        return 2
    if not TEMPLATE.exists():
        print(f"ERROR: template missing at {TEMPLATE}", file=sys.stderr)
        print("       run scripts/_build_template.py to (re)derive it", file=sys.stderr)
        return 2

    data = json.loads(analysis_path.read_text())

    # insights.json — what the HTML reads
    insights_data = {k: data[k] for k in ("video", "thesis", "topics", "chapters", "insights", "quotes", "resources", "action_items", "stats", "charts") if k in data}
    insights_data.setdefault("topics", [])
    insights_data.setdefault("charts", [])
    write_json(vdir / "insights.json", insights_data)

    # entities.json
    ents = data.get("entities", {})
    entities_data = {
        "video_id": args.video_id,
        "people": ents.get("people", []),
        "companies": ents.get("companies", []),
        "products_tech": ents.get("products_tech", []),
        "frameworks_concepts": ents.get("frameworks_concepts", []),
        "books_papers": ents.get("books_papers", []),
    }
    write_json(vdir / "entities.json", entities_data)

    # predictions.json
    predictions_data = {
        "video_id": args.video_id,
        "predictions": data.get("predictions", []),
        "validated_predictions": data.get("validated_predictions", []),
    }
    write_json(vdir / "predictions.json", predictions_data)

    # notes.md (don't overwrite)
    notes = vdir / "notes.md"
    if not notes.exists():
        notes.write_text("# Notes\n\n")

    # Render index.html — the template embeds DATA via sentinel substitution
    template = TEMPLATE.read_text()
    data_json = json.dumps(insights_data, indent=2, ensure_ascii=False)
    html = template.replace("/*__DATA_JSON__*/null", data_json)
    if "/*__DATA_JSON__*/" in html or "null" not in template:
        # If the sentinel didn't get replaced, the template is out of date.
        if "/*__DATA_JSON__*/null" in template:
            pass  # Was replaced cleanly.
        else:
            print("WARN: template sentinel not found — index.html may render empty", file=sys.stderr)

    (vdir / "index.html").write_text(html)

    print(f"  Wrote {vdir / 'index.html'}")
    print(f"    insights={len(insights_data.get('insights', []))} "
          f"entities={sum(len(v) for v in entities_data.values() if isinstance(v, list))} "
          f"predictions={len(predictions_data['predictions'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
