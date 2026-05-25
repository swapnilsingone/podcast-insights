# Podcast Insights — project guide for Claude

This project is a personal archive of interactive insight artifacts built from YouTube videos and podcasts. Each processed video becomes a single-file HTML page; the `library/` folder is a gallery + a suite of cross-archive views (tracker, entities, predictions, speakers, ask, compare, highlights).

## Session-start reminder (do this first, every session)

**Before responding to the user's first message in a session opened in this project**, do a quick compounding check and surface it briefly:

1. Read `./library/tracker.json`. If it doesn't exist or `snapshot_at` is older than 24 hours, run `python3 scripts/build_indexes.py` first to refresh.
2. From the refreshed `tracker.json`, compute and surface in 3–5 short lines, only if there is something worth saying:
   - **Overdue predictions**: count from `predictions_overdue`. If > 0, name one and link `library/predictions.html`.
   - **New recurring entities**: anyone in `recurring_entities` whose `video_count` increased since the previous tracker snapshot (compare against a copy saved at `./library/.last_seen_tracker.json` if it exists).
   - **Backlog**: count of non-comment lines in `queue.txt` not yet under `videos/`.
   - **Stale**: if the most recent `processed_at` in `library.json` is > 21 days old, gently nudge.
3. End with: `Tracker → library/dashboard.html`.
4. Then save the current tracker as `./library/.last_seen_tracker.json` (overwrite) so the next session sees the delta.
5. **Be brief.** Do not show the report unless there's at least one signal.

## How videos get processed

There are two paths. They share the same outputs.

### Path A — autonomous (default): `./scripts/process_queue.sh`

Drop URLs into `queue.txt`, run the script, walk away. The script handles **everything**: yt-dlp metadata + audio → Groq transcription → `claude -p` analysis → render index.html from the locked template → append to `history.jsonl` → create a readable symlink → rebuild library indexes → de-queue.

```bash
./scripts/process_queue.sh
```

**Locked policy — no flags, no fallbacks:**

- **Model:** `claude-opus-4-7`. The script enforces this. Do not pass model arguments and do not export `CLAUDE_MODEL` to override.
- **Transcription:** Groq only (`whisper-large-v3`). Requires `GROQ_API_KEY` from `../.env.local`. No mlx fallback, no YouTube-caption fallback.

If either the model or Groq transcription is unavailable/fails, the URL is **not processed**, and the queue line is rewritten with the specific failure marker so you know what to fix:

| Marker in `queue.txt` | Meaning |
|---|---|
| `# FAILED-TRANSCRIPTION: <url>` | Groq transcription failed (missing `GROQ_API_KEY`, API error, quota, etc.) |
| `# FAILED-MODEL: <url>` | `claude -p` invocation with `claude-opus-4-7` failed |
| `# FAILED-MODEL+TRANSCRIPTION: <url>` | Both failed |
| `# FAILED-METADATA: <url>` | yt-dlp could not fetch metadata (precedes transcription/model) |

Successful URLs are removed from `queue.txt`. To retry a failed URL, fix the underlying issue and uncomment the line.

### Path B — interactive: tell Claude "process the queue"

In a Claude Code session, say "process the queue" or paste a URL. Claude runs the same pipeline step-by-step in-session. Use this when you want to inspect intermediate output (transcripts, draft insights) before committing, or when you don't want to spend `claude -p` tokens.

## Locked design — do NOT redesign per video

The video and library page designs are **locked**. The canonical template is `scripts/templates/video_template.html` and is rendered by `scripts/build_video.py`. The library at `library/index.html` is data-driven from `library/*.json`.

**Do not invoke the `frontend-design` skill when processing a video.** Do not invent new visual treatments. The template covers:

- Topbar with `← Library` back-link, theme toggle, "open on YouTube" icon
- Hero with embedded YouTube iframe (JS-controllable via the IFrame Player API)
- Click-to-seek on every timestamp anchor + floating PiP when scrolled past
- Insight density timeline with category-colored dots
- Bento card grid (12-col, dense flow, mixed `size-md` cards for editorial rhythm)
- Per-card `+ note` (localStorage), `⚔ steelman` (Claude prompt to clipboard), and `copy` buttons
- Pull-quote interleaving every 6 cards
- "By the numbers" stat grid (4 stats), three charts (Chart.js), resources, action items
- Dark theme default with light toggle, persisted to localStorage

If the template needs to evolve, edit `scripts/templates/video_template.html` directly. The bootstrap script `scripts/_build_template.py` was the one-shot used to derive it from `videos/HGbA6ze0_3M/index.html` and is **not** part of the regular pipeline — only run it again if you want to re-bootstrap from a hand-edited canonical page.

## The pipeline scripts

| Script | What it does |
|---|---|
| `scripts/process_queue.sh` | Orchestrator. Iterates `queue.txt` and calls everything below. |
| `scripts/transcribe.py <id>` | Audio download + transcription. Groq if `GROQ_API_KEY`, else mlx-whisper. |
| `scripts/analyze.py <id>` | Calls `claude -p` with the transcript + a strict schema. Writes `videos/<id>/analysis.json`. |
| `scripts/build_video.py <id>` | Splits `analysis.json` into `insights.json` / `entities.json` / `predictions.json` and renders `index.html` from the locked template. |
| `scripts/build_indexes.py` | Rebuilds all `library/*.json` from per-video files. Idempotent. |
| `scripts/retrofit_player.py` | One-off patcher for older artifacts that lacked the embedded-player CSS. Idempotent. |
| `scripts/serve.py` | Static HTTP server on `:8765` for the library pages (Chrome blocks `fetch()` over `file://`). |
| `scripts/make_highlight.py <id>` | Cut a highlight reel via yt-dlp + ffmpeg. |
| `scripts/ingest_feeds.py` | Read `feeds.txt`, append new YouTube uploads to `queue.txt`. |

## Time fields — what means what

- `upload_date` — when the conversation was published on YouTube. Canonical event time.
- `processed_at` — when *we* archived it locally. Stored in `library.json` and `history.jsonl`.
- `processing_lag_days` — `processed_at − upload_date`. Computed by `build_indexes.py`.
- `stated_at` (predictions) — equals the source video's `upload_date`. Anchor for when the claim was made.
- `revisit_dates` (predictions) — ISO dates by which to score. Always offset from `stated_at`, not `processed_at`.

## Library pages — must be served over HTTP, not file://

Chrome blocks `fetch()` between local files. Per-video artifacts work over `file://` because data is inlined, but `library/*` pages all `fetch()` JSON and **silently fail** when opened directly.

```bash
lsof -i :8765 >/dev/null || python3 scripts/serve.py &
open http://localhost:8765/library/index.html
```

Library pages and what they read:

- `index.html` — gallery (reads `library.json` + `insights.json`)
- `dashboard.html` — compounding tracker (reads `tracker.json`, `predictions.json`, `library.json`)
- `entities.html` / `predictions.html` / `speakers.html` / `qa.html` / `compare.html` / `highlights.html` — each reads its named JSON

These pages are **stable** — don't edit them per video. Regenerate only if the data schema changes.

## Model choice — locked to Opus 4.7

The pipeline is **locked to `claude-opus-4-7`** for analysis. Reason: this archive prioritizes insight quality over speed/cost — Opus picks sharper non-obvious insights, tighter theses, and more memorable quotes. The queue script enforces this; do not pass model overrides.

Reference (for context only — not a switchable setting):

| Aspect | Sonnet 4.6 | Opus 4.7 (locked) |
|---|---|---|
| Insight selection (non-obvious vs restatement) | Good; sometimes includes 1–2 "obvious" cards | Sharper; better filter for what's non-obvious |
| Quote selection (punch + non-redundancy) | Good | Slightly better — picks more memorable lines |
| Thesis framing | Sometimes generic | Tighter, more compressed |
| Speed / cost per video | ~2–3× faster, ~5× cheaper | Baseline |

If Opus is genuinely unavailable, the URL fails with `# FAILED-MODEL:` rather than silently downgrading. Fix the auth/availability issue, don't switch models.

## Transcription policy — Groq only

`./scripts/process_queue.sh` uses **Groq Whisper (`whisper-large-v3`) and nothing else**. No mlx fallback, no auto-caption fallback. If Groq is unavailable (missing `GROQ_API_KEY`, API error, quota), the URL is marked `# FAILED-TRANSCRIPTION:` in `queue.txt` and is not processed.

- `GROQ_API_KEY` must be set (lives in `../.env.local`, mode 600). The script sources it automatically.
- Audio is split into <25 MB chunks (~22 min each). 1h transcribes in ~30s.
- Free tier: 7,200 audio-seconds/day. If you hit it, wait or upgrade — don't fall back.

Whisper does NOT give you speaker labels. For multi-host podcasts attribute by context only; when unsure use "show hosts" or the named guest.

The standalone `scripts/transcribe.py` still supports an `mlx` backend for one-off manual use, but the queue pipeline never invokes it.

## Don't

- Don't redesign the per-video page. Edit `scripts/templates/video_template.html` instead.
- Don't invoke the `frontend-design` skill from the youtube-insights flow — the design is locked.
- Don't write outputs to `/tmp` while inside this project.
- Don't overwrite `notes.md` or `library/scores.json`.
- Don't truncate `history.jsonl` — append-only.
- Don't rename per-video folders away from the 11-char YouTube ID; `videos/<title-slug>` symlinks are fine and `build_indexes.py` skips symlinks automatically.
- Don't paste large transcripts into chat — the artifact is the deliverable.
- Don't skip the session-start tracker check unless there's truly nothing to surface.
