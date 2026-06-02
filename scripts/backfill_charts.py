#!/usr/bin/env python3
"""Charts-only backfill for already-archived videos (non-destructive).

For each video missing `charts`, send ONLY its thesis + stats + insights
(headline/body) to Claude and ask for 0-3 charts grounded in those numbers.
Merge the result into videos/<id>/insights.json and analysis.json WITHOUT
touching insights, quotes, entities, or predictions. Then run build_video.py
to re-render, and build_indexes.py.

Episodes with no real quantitative content correctly get `[]` (section hides).

Usage:
  python3 scripts/backfill_charts.py                 # videos with no charts yet
  python3 scripts/backfill_charts.py --all           # re-do every video
  python3 scripts/backfill_charts.py --only <id> ... # specific videos
  python3 scripts/backfill_charts.py --dry-run       # print, write nothing
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEOS = ROOT / "videos"

SYSTEM = ("You are a precise data-viz editor. From a podcast episode's distilled "
          "insights you extract only charts that are genuinely supported by numbers "
          "stated in the material. You never invent figures. Output ONLY JSON.")

PROMPT_TMPL = """Below is the distilled content of one episode: its thesis, the
most-citable stats, and the insight headlines/bodies. Produce charts ONLY where
these supply real comparative numbers worth visualizing (segment values, a trend
over time, a ranking, before/after).

Rules:
- 0-3 charts. Use ONLY numbers present in the material below — never invent or
  import figures from elsewhere. If there is nothing quantitative worth charting,
  return an empty list. An honest empty list is the correct answer for
  interview/theory episodes.
- `type` is "bar", "line" (ordered x-axis, e.g. time), or "hbar" (horizontal bar /
  ranking). `labels` align 1:1 with each series' `data`; use null for a missing point.
- Put the unit (%, $B, days) in `unit` AND in each series `name`.
- `caption` cites the speaker/source and an approximate timestamp if available.

EPISODE TITLE: {title}
THESIS: {thesis}

STATS:
{stats}

INSIGHTS:
{insights}

Output a SINGLE JSON object: {{"charts": [ ... ]}} matching this shape per chart:
{{"type":"bar","title":"...","sub":"...","caption":"Source: ... · MM:SS","unit":"$B","labels":["A","B"],"series":[{{"name":"X ($B)","data":[1,2]}}]}}
Output ONLY the JSON object. No prose, no backticks."""


def fmt_stats(stats):
    if not stats:
        return "(none)"
    return "\n".join(
        f'- {s.get("label","?")}: {s.get("val","")}{(" " + s["unit"]) if s.get("unit") else ""}'
        f'{(" — " + s["sub"]) if s.get("sub") else ""}'
        for s in stats
    )


def fmt_insights(insights):
    return "\n".join(
        f'- [{i.get("start_seconds",0)}s] {i.get("headline","")} — {i.get("body","")}'
        for i in insights
    )


def gen_charts(video, thesis, stats, insights, model, timeout=300):
    prompt = PROMPT_TMPL.format(
        title=video.get("title", ""),
        thesis=thesis or "",
        stats=fmt_stats(stats),
        insights=fmt_insights(insights),
    )
    res = subprocess.run(
        ["claude", "-p", "--output-format", "text",
         "--append-system-prompt", SYSTEM, "--model", model],
        input=prompt, capture_output=True, text=True, timeout=timeout,
    )
    if res.returncode != 0:
        raise RuntimeError(f"claude exit {res.returncode}: {res.stderr[:300]}")
    raw = res.stdout.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw).strip()
    s, e = raw.find('{'), raw.rfind('}')
    if s == -1 or e == -1:
        raise ValueError(f"no JSON: {raw[:200]}")
    obj = json.loads(raw[s:e + 1])
    charts = obj.get("charts", [])
    # Light validation: keep charts whose labels line up with each series.
    clean = []
    for c in charts if isinstance(charts, list) else []:
        if not isinstance(c, dict) or not c.get("labels"):
            continue
        series = c.get("series") or ([{"name": c.get("title", ""), "data": c["data"]}] if c.get("data") else [])
        n = len(c["labels"])
        series = [s for s in series if isinstance(s.get("data"), list) and len(s["data"]) == n]
        if not series:
            continue
        c["series"] = series
        c.pop("data", None)
        if c.get("type") not in ("bar", "line", "hbar"):
            c["type"] = "bar"
        clean.append(c)
    return clean[:3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default=os.environ.get("CLAUDE_MODEL", "claude-opus-4-8"))
    args = ap.parse_args()

    targets = []
    for vd in sorted(d for d in VIDEOS.iterdir() if d.is_dir() and not d.is_symlink()):
        if args.only and vd.name not in args.only:
            continue
        ins_path = vd / "insights.json"
        if not ins_path.exists():
            continue
        ins = json.loads(ins_path.read_text())
        if not args.all and not args.only and ins.get("charts"):
            continue
        targets.append((vd, ins))

    if not targets:
        print("Nothing to do (all have charts; use --all or --only).")
        return 0

    print(f"Charts backfill for {len(targets)} video(s) via {args.model}…\n")
    wrote = 0
    for vd, ins in targets:
        vid = vd.name
        title = ins.get("video", {}).get("title", vid)[:55]
        try:
            charts = gen_charts(ins.get("video", {}), ins.get("thesis"),
                                ins.get("stats", []), ins.get("insights", []), args.model)
        except Exception as e:
            print(f"  ✗ {vid}  {title}\n      {e}", file=sys.stderr)
            continue
        kinds = ", ".join(f'{c["type"]}:{c["title"][:30]}' for c in charts) or "(none — left empty)"
        print(f"  {vid}  {len(charts)} chart(s)  {title}\n      {kinds}")
        if args.dry_run:
            continue
        for fname in ("insights.json", "analysis.json"):
            p = vd / fname
            if not p.exists():
                continue
            data = json.loads(p.read_text())
            data["charts"] = charts
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            wrote += 1

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0
    print(f"\nWrote charts into {wrote} file(s).")
    print("Next: re-render each video (build_video.py <id>) and rebuild indexes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
