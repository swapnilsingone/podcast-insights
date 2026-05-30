#!/usr/bin/env python3
"""Aggregate per-video JSON files into library-level indexes.

Reads:
  videos/<id>/insights.json
  videos/<id>/entities.json
  videos/<id>/predictions.json

Writes:
  library/library.json     — all videos (gallery)
  library/entities.json    — entity rollup
  library/predictions.json — prediction tracker
  library/speakers.json    — speaker dossiers
  library/insights.json    — flat list of every insight + quote across the archive (for QA/search)
  library/tracker.json     — snapshot for the dashboard

Run: python3 scripts/build_indexes.py
Idempotent. Safe to run after every video added.
"""
import json, os, sys, glob, datetime
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
VIDEOS = ROOT / "videos"
LIB = ROOT / "library"
LIB.mkdir(exist_ok=True)

def load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default

def slug(s):
    return "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-")

def parse_upload_date(s):
    """yt-dlp returns YYYYMMDD; project files use YYYY-MM-DD. Accept both."""
    if not s: return None
    s = str(s).replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None

def main():
    video_dirs = sorted([d for d in VIDEOS.iterdir() if d.is_dir() and not d.is_symlink()])

    library = []
    entity_rollup = defaultdict(lambda: {"name": "", "kind": "", "occurrences": [], "total_mentions": 0,
                                          "first_uploaded": None, "last_uploaded": None, "upload_dates": set()})
    predictions = []
    validated = []
    speaker_rollup = defaultdict(lambda: {"name": "", "role": "", "appearances": [], "claims": [],
                                           "first_uploaded": None, "last_uploaded": None})
    all_insights = []
    all_quotes = []

    for vd in video_dirs:
        vid = vd.name
        ins = load_json(vd / "insights.json")
        if not ins:
            continue
        v = ins.get("video", {})
        upload_date = parse_upload_date(v.get("upload_date"))

        processed_at = datetime.datetime.fromtimestamp(
            (vd / "insights.json").stat().st_mtime, tz=datetime.timezone.utc
        ).isoformat().replace("+00:00", "Z")

        # Lag: days between upload and archive
        lag_days = None
        if upload_date:
            try:
                up = datetime.date.fromisoformat(upload_date)
                pr = datetime.datetime.fromisoformat(processed_at.replace("Z", "+00:00")).date()
                lag_days = (pr - up).days
            except Exception:
                lag_days = None

        # Library entry
        library.append({
            "video_id": vid,
            "url": v.get("url"),
            "title": v.get("title"),
            "channel": v.get("channel"),
            "thumbnail": v.get("thumbnail"),
            "duration_seconds": v.get("duration_seconds"),
            "upload_date": upload_date,
            "thesis": ins.get("thesis"),
            "topics": ins.get("topics", []),
            "insight_count": len(ins.get("insights", [])),
            "quote_count": len(ins.get("quotes", [])),
            "processed_at": processed_at,
            "processing_lag_days": lag_days,
            "path": f"../videos/{vid}/index.html"
        })

        # Flat insights/quotes for QA
        for i in ins.get("insights", []):
            all_insights.append({
                **i,
                "video_id": vid,
                "video_title": v.get("title"),
                "channel": v.get("channel"),
                "url": f"{v.get('url')}&t={int(i.get('start_seconds', 0))}s",
                "speaker": "Jensen Huang" if vid == "gwW8GKwHB3I" else None
            })
        for q in ins.get("quotes", []):
            all_quotes.append({
                **q,
                "video_id": vid,
                "video_title": v.get("title"),
                "channel": v.get("channel"),
                "url": f"{v.get('url')}&t={int(q.get('start_seconds', 0))}s"
            })

        # Entities
        ents = load_json(vd / "entities.json", {})
        for kind in ("people", "companies", "products_tech", "frameworks_concepts", "books_papers"):
            for e in ents.get(kind, []):
                key = (kind, slug(e["name"]))
                er = entity_rollup[key]
                er["name"] = e["name"]
                er["kind"] = kind
                er["occurrences"].append({
                    "video_id": vid,
                    "video_title": v.get("title"),
                    "channel": v.get("channel"),
                    "upload_date": upload_date,
                    "url": f"{v.get('url')}&t={int(e.get('first_seconds', 0))}s",
                    "first_seconds": e.get("first_seconds", 0),
                    "mentions": e.get("mentions", 1),
                    "role": e.get("role"),
                    "context": e.get("context")
                })
                er["total_mentions"] += e.get("mentions", 1)
                if upload_date:
                    er["upload_dates"].add(upload_date)
                    if not er["first_uploaded"] or upload_date < er["first_uploaded"]:
                        er["first_uploaded"] = upload_date
                    if not er["last_uploaded"] or upload_date > er["last_uploaded"]:
                        er["last_uploaded"] = upload_date

        # Speakers — people with speaker=True
        for p in ents.get("people", []):
            if p.get("speaker"):
                key = slug(p["name"])
                s = speaker_rollup[key]
                s["name"] = p["name"]
                s["role"] = p.get("role", s.get("role", ""))
                s["appearances"].append({
                    "video_id": vid,
                    "video_title": v.get("title"),
                    "channel": v.get("channel"),
                    "url": v.get("url"),
                    "upload_date": upload_date,
                    "thumbnail": v.get("thumbnail")
                })
                if upload_date:
                    if not s["first_uploaded"] or upload_date < s["first_uploaded"]:
                        s["first_uploaded"] = upload_date
                    if not s["last_uploaded"] or upload_date > s["last_uploaded"]:
                        s["last_uploaded"] = upload_date

        # Speakers — link their claims (use insights of the video; assume guest speaker if 1)
        # Heuristic: in v1 we attribute all insights to the lone "guest" speaker if marked
        guest = next((p["name"] for p in ents.get("people", []) if p.get("speaker") and "Host" not in (p.get("role") or "")), None)
        if guest:
            sk = speaker_rollup[slug(guest)]
            for i in ins.get("insights", []):
                sk["claims"].append({
                    "video_id": vid,
                    "video_title": v.get("title"),
                    "headline": i["headline"],
                    "category": i["category"],
                    "start_seconds": i["start_seconds"],
                    "url": f"{v.get('url')}&t={int(i['start_seconds'])}s"
                })

        # Predictions
        preds = load_json(vd / "predictions.json", {})
        for p in preds.get("predictions", []):
            predictions.append({
                **p,
                "video_id": vid,
                "video_title": v.get("title"),
                "channel": v.get("channel"),
                "url": f"{v.get('url')}&t={int(p['start_seconds'])}s"
            })
        for p in preds.get("validated_predictions", []):
            validated.append({**p, "video_id": vid, "video_title": v.get("title")})

    # Sort — primary timeline is the YouTube upload_date when available
    library.sort(key=lambda x: (x.get("upload_date") or x.get("processed_at") or ""), reverse=True)
    predictions.sort(key=lambda x: (x.get("revisit_dates") or ["9999"])[0])

    entities_out = []
    for (kind, key), er in entity_rollup.items():
        er["video_count"] = len({o["video_id"] for o in er["occurrences"]})
        er["slug"] = key
        er["upload_dates"] = sorted(er.pop("upload_dates"))  # set → sorted list
        er["span_days"] = (
            (datetime.date.fromisoformat(er["last_uploaded"]) - datetime.date.fromisoformat(er["first_uploaded"])).days
            if er["first_uploaded"] and er["last_uploaded"] else 0
        )
        # Sort occurrences chronologically by upload_date
        er["occurrences"].sort(key=lambda o: o.get("upload_date") or "")
        entities_out.append(er)
    entities_out.sort(key=lambda e: (-e["video_count"], -e["total_mentions"]))

    speakers_out = []
    for key, s in speaker_rollup.items():
        s["slug"] = key
        s["appearance_count"] = len(s["appearances"])
        s["claim_count"] = len(s["claims"])
        s["appearances"].sort(key=lambda a: a.get("upload_date") or "", reverse=True)
        s["span_days"] = (
            (datetime.date.fromisoformat(s["last_uploaded"]) - datetime.date.fromisoformat(s["first_uploaded"])).days
            if s["first_uploaded"] and s["last_uploaded"] else 0
        )
        speakers_out.append(s)
    speakers_out.sort(key=lambda s: (-s["appearance_count"], -s["claim_count"]))

    # Tracker snapshot — what's compounding
    now = datetime.datetime.now(datetime.timezone.utc)
    soon = now + datetime.timedelta(days=90)
    recent = now - datetime.timedelta(days=14)
    topic_counts = defaultdict(int)
    for v in library:
        for t in v.get("topics", []):
            topic_counts[t] += 1
    topics_summary = [{"topic": t, "video_count": n}
                      for t, n in sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    recurring_entities = [e for e in entities_out if e["video_count"] >= 2]
    recurring_speakers = [s for s in speakers_out if s["appearance_count"] >= 2]
    due_predictions = [p for p in predictions if p.get("status") == "open" and any(
        d <= soon.date().isoformat() for d in (p.get("revisit_dates") or []))]
    overdue_predictions = [p for p in predictions if p.get("status") == "open" and any(
        d <= now.date().isoformat() for d in (p.get("revisit_dates") or []))]
    new_videos = [v for v in library if v.get("processed_at", "") >= recent.isoformat().replace("+00:00", "Z")]

    # Upload-date stats
    uploads = sorted([v.get("upload_date") for v in library if v.get("upload_date")])
    lags = [v.get("processing_lag_days") for v in library if v.get("processing_lag_days") is not None]
    upload_stats = {
        "oldest_upload": uploads[0] if uploads else None,
        "newest_upload": uploads[-1] if uploads else None,
        "span_days": (datetime.date.fromisoformat(uploads[-1]) - datetime.date.fromisoformat(uploads[0])).days if len(uploads) >= 2 else 0,
        "avg_processing_lag_days": round(sum(lags) / len(lags), 1) if lags else None,
        "median_processing_lag_days": sorted(lags)[len(lags)//2] if lags else None,
        "by_month": uploads_by_month(library)
    }
    # Freshest video the user has *not* yet processed (left to caller; we just publish what we know)
    recently_uploaded = sorted(
        [v for v in library if v.get("upload_date")],
        key=lambda v: v["upload_date"], reverse=True
    )[:5]

    tracker = {
        "snapshot_at": now.isoformat().replace("+00:00", "Z"),
        "totals": {
            "videos": len(library),
            "insights": sum(v["insight_count"] for v in library),
            "quotes": sum(v.get("quote_count", 0) for v in library),
            "hours_archived": round(sum((v.get("duration_seconds") or 0) for v in library) / 3600, 1),
            "channels": len({v.get("channel") for v in library if v.get("channel")}),
            "entities": len(entities_out),
            "speakers": len(speakers_out),
            "predictions_open": sum(1 for p in predictions if p.get("status") == "open"),
            "predictions_validated": len(validated)
        },
        "uploads": upload_stats,
        "topics": topics_summary,
        "recurring_entities": recurring_entities[:20],
        "recurring_speakers": recurring_speakers[:10],
        "predictions_due_within_90d": due_predictions,
        "predictions_overdue": overdue_predictions,
        "recent_videos": new_videos[:5],
        "recently_uploaded": recently_uploaded,
        "growth_by_day": growth_by_day(library)
    }

    # Write
    (LIB / "library.json").write_text(json.dumps(library, indent=2))
    (LIB / "entities.json").write_text(json.dumps(entities_out, indent=2))
    (LIB / "speakers.json").write_text(json.dumps(speakers_out, indent=2))
    (LIB / "predictions.json").write_text(json.dumps({"open": predictions, "validated": validated}, indent=2))
    (LIB / "insights.json").write_text(json.dumps({"insights": all_insights, "quotes": all_quotes}, indent=2))
    (LIB / "tracker.json").write_text(json.dumps(tracker, indent=2))

    print(f"Built library indexes:")
    print(f"  videos:      {len(library)}")
    print(f"  insights:    {tracker['totals']['insights']}")
    print(f"  entities:    {len(entities_out)} (recurring: {len(recurring_entities)})")
    print(f"  speakers:    {len(speakers_out)} (recurring: {len(recurring_speakers)})")
    print(f"  predictions: {tracker['totals']['predictions_open']} open, {len(overdue_predictions)} overdue, {len(due_predictions)} due in 90d")

def growth_by_day(library):
    counts = defaultdict(lambda: {"videos": 0, "insights": 0})
    for v in library:
        d = (v.get("processed_at") or "")[:10]
        if not d: continue
        counts[d]["videos"] += 1
        counts[d]["insights"] += v.get("insight_count", 0)
    return [{"date": k, **counts[k]} for k in sorted(counts.keys())]

def uploads_by_month(library):
    counts = defaultdict(int)
    for v in library:
        d = v.get("upload_date")
        if not d: continue
        counts[d[:7]] += 1
    return [{"month": m, "videos": counts[m]} for m in sorted(counts.keys())]

if __name__ == "__main__":
    main()
