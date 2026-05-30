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

Two paths, same outputs.

### Path A — autonomous (default): `./scripts/process_queue.sh`

Drop URLs into `queue.txt`, run the script, walk away. Handles everything: yt-dlp → transcription → `claude -p` analysis → render → append to `history.jsonl` → symlink → rebuild indexes → de-queue.

**Locked policy:**
- **Model:** `claude-opus-4-8`. Hardcoded. Do not pass model arguments or export `CLAUDE_MODEL`.
- **Default transcription:** `whisperx`. Switchable per run (`TRANSCRIPTION_DEFAULT=groq`) or per URL.

Per-URL override syntax in `queue.txt`:

```
https://www.youtube.com/watch?v=...               # uses TRANSCRIPTION_DEFAULT (whisperx)
[groq]     https://www.youtube.com/watch?v=...    # force Groq (fast, no speakers)
[whisperx] https://www.youtube.com/watch?v=...    # force WhisperX (slow, with speakers)
```

Failure markers written back to `queue.txt`:

| Marker | Meaning |
|---|---|
| `# FAILED-TRANSCRIPTION: <line>` | Transcription failed |
| `# FAILED-MODEL: <line>` | `claude -p` with `claude-opus-4-8` failed |
| `# FAILED-MODEL+TRANSCRIPTION: <line>` | Both failed |
| `# FAILED-METADATA: <line>` | yt-dlp could not fetch metadata |

The `[backend]` prefix is preserved so uncommenting retries with the same backend.

### Path B — interactive: tell Claude "process the queue"

Claude runs the same pipeline step-by-step in-session. Use when you want to inspect intermediate output or avoid spending `claude -p` tokens.

## Locked design — do NOT redesign per video

The video and library page designs are **locked**. Canonical template: `scripts/templates/video_template.html`, rendered by `scripts/build_video.py`.

**Do not invoke the `frontend-design` skill when processing a video.** To evolve the design, edit `scripts/templates/video_template.html` directly.

## The pipeline scripts

| Script | What it does |
|---|---|
| `scripts/process_queue.sh` | Orchestrator. Iterates `queue.txt`, routes each URL to the chosen transcription backend, then calls analyze + build + post-process. |
| `scripts/transcribe.py <id>` | Groq (default) or legacy mlx-whisper. Used for `[groq]` URLs. |
| `scripts/transcribe_whisperx.py <id>` | **Runs in `.venv`.** Whisper large-v3 + alignment + pyannote diarization. Used for `[whisperx]` URLs. |
| `scripts/analyze.py <id>` | Calls `claude -p` with the transcript + a strict schema. Writes `videos/<id>/analysis.json`. |
| `scripts/build_video.py <id>` | Splits `analysis.json` into `insights.json` / `entities.json` / `predictions.json` and renders `index.html`. |
| `scripts/build_indexes.py` | Rebuilds all `library/*.json` from per-video files. Idempotent. |
| `scripts/serve.py` | Static HTTP server on `:8765`. See `instructions.md` for startup commands. |
| `scripts/make_highlight.py <id>` | Cut a highlight reel via yt-dlp + ffmpeg. |
| `scripts/ingest_feeds.py` | Read `feeds.txt`, append new YouTube uploads to `queue.txt`. |

## Time fields — what means what

- `upload_date` — when the video was published on YouTube. Canonical event time.
- `processed_at` — when we archived it locally.
- `processing_lag_days` — `processed_at − upload_date`. Computed by `build_indexes.py`.
- `stated_at` (predictions) — equals the source video's `upload_date`.
- `revisit_dates` (predictions) — ISO dates offset from `stated_at`, not `processed_at`.

## Library pages — must be served over HTTP, not file://

Chrome blocks `fetch()` between local files. Library pages silently fail when opened directly. See `instructions.md` for server startup commands.

## Model choice — locked to Opus 4.8

Locked to `claude-opus-4-8` for insight quality. If unavailable, the URL fails with `# FAILED-MODEL:` — fix auth, don't switch models.

## Transcription backends

| Backend | Speakers | Speed | Cost | When to use |
|---|---|---|---|---|
| **WhisperX** (default) | Yes | ~60–100 min/hr audio | $0 | Multi-speaker podcasts |
| **Groq** | No | ~30s/hr audio | Free tier | Solo narration, fast turnaround |

WhisperX runs in `.venv` and requires `HF_TOKEN` in `../.env.local`. See `SETUP.md`. No fallback — missing token → `# FAILED-TRANSCRIPTION:`.

Groq requires `GROQ_API_KEY` in `../.env.local`. No speaker labels.

## Don't

- Don't redesign the per-video page. Edit `scripts/templates/video_template.html` instead.
- Don't invoke the `frontend-design` skill from the youtube-insights flow.
- Don't write outputs to `/tmp` while inside this project.
- Don't overwrite `notes.md` or `library/scores.json`.
- Don't truncate `history.jsonl` — append-only.
- Don't rename per-video folders away from the 11-char YouTube ID.
- Don't paste large transcripts into chat — the artifact is the deliverable.
- Don't skip the session-start tracker check unless there's truly nothing to surface.
- Don't commit `.venv/` (it's ~1.2 GB; covered by `.gitignore`).
- Don't invoke `scripts/transcribe_whisperx.py` with system `python3` — use `.venv/bin/python`.
- Don't pass large prompts (~50 KB+) to `claude -p` via argv — pipe via stdin. See `GOTCHAS.md`.

## Gotchas

Non-obvious traps — `claude -p` SIGKILL on big argv, HuggingFace 403 chains, pyenv `_lzma` builds, etc. See **`GOTCHAS.md`** before debugging anything similar.
