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

**Locked policy:**

- **Model:** `claude-opus-4-7`. Hardcoded in the script. Do not pass model arguments and do not export `CLAUDE_MODEL` to override.
- **Default transcription backend:** `whisperx` (local Whisper large-v3 + pyannote speaker diarization). Switchable per run (`TRANSCRIPTION_DEFAULT=groq`) or per URL (see below).

Per-URL override syntax in `queue.txt`:

```
https://www.youtube.com/watch?v=...               # uses TRANSCRIPTION_DEFAULT (whisperx)
[groq]     https://www.youtube.com/watch?v=...    # force Groq (fast, no speakers)
[whisperx] https://www.youtube.com/watch?v=...    # force WhisperX (slow, with speakers)
```

If transcription or analysis fails, the queue line is rewritten with one of:

| Marker in `queue.txt` | Meaning |
|---|---|
| `# FAILED-TRANSCRIPTION: <line>` | Transcription failed (groq key/api/quota, or whisperx HF gate/venv) |
| `# FAILED-MODEL: <line>` | `claude -p` with `claude-opus-4-7` failed |
| `# FAILED-MODEL+TRANSCRIPTION: <line>` | Both preflight checks failed |
| `# FAILED-METADATA: <line>` | yt-dlp could not fetch metadata |

The `[backend]` prefix is preserved in the marker, so uncommenting retries with the same backend choice. Successful URLs are removed from `queue.txt`.

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
| `scripts/process_queue.sh` | Orchestrator. Iterates `queue.txt`, routes each URL to the chosen transcription backend, then calls analyze + build + post-process. |
| `scripts/transcribe.py <id>` | Groq (default) or legacy mlx-whisper. Used for `[groq]` URLs. |
| `scripts/transcribe_whisperx.py <id>` | **Runs in `.venv`.** Whisper large-v3 + alignment + pyannote diarization. Used for `[whisperx]` URLs. Output transcript includes speaker labels. See `SETUP.md`. |
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

## Transcription policy — WhisperX default, Groq on demand

There are two supported backends. Both produce the same downstream artifacts (`transcript.txt`, `transcript.json`) that `analyze.py` consumes; only WhisperX includes speaker labels.

| Backend | Speakers | Wall time per hour of audio | Cost | When to use |
|---|---|---|---|---|
| **WhisperX** (default) | Yes (pyannote `community-1`) | ~60–100 min on M-series CPU | $0 | Multi-speaker podcasts and interviews — anywhere quotes need real attribution |
| **Groq** (`whisper-large-v3`) | No | ~30s (200× realtime) | Free tier 7,200s/day | Solo narration, news monologues, fast turnaround |

**Choosing the backend:**

- Default for the whole run: `TRANSCRIPTION_DEFAULT=whisperx` (hardcoded in `process_queue.sh`). Flip per run with `TRANSCRIPTION_DEFAULT=groq ./scripts/process_queue.sh`.
- Per-URL override: prefix the queue.txt line with `[groq]` or `[whisperx]`.

**WhisperX details:**

- Runs in the project's `.venv` on Python 3.12 (kept separate from your shell's pyenv so torch/pyannote don't pollute global). The queue script invokes `.venv/bin/python scripts/transcribe_whisperx.py`.
- Requires `HF_TOKEN` (read scope) in `../.env.local`, plus accepting two HF gates: `pyannote/segmentation-3.0` and `pyannote/speaker-diarization-community-1`. See `SETUP.md`.
- Model: `whisperx.load_model("large-v3", device="cpu", compute_type="int8")`, alignment via `whisperx.load_align_model`, diarization via `whisperx.diarize.DiarizationPipeline`. Output segments include a `speaker` field; `transcript.txt` formats lines as `[hh:mm:ss] SPEAKER_xx: text`.
- No fallback. If `HF_TOKEN` is missing or diarization fails, the URL is marked `# FAILED-TRANSCRIPTION:` and not processed (`--require-diarize` is passed by the queue script).

**Groq details:**

- `GROQ_API_KEY` must be set in `../.env.local`. Audio is split into <25 MB chunks (~22 min) and uploaded.
- No speaker labels. `analyze.py` will attribute quotes by context only — for multi-host podcasts you'll lose precision in the "speaker" field of quotes.

The standalone `scripts/transcribe.py` (Groq + a legacy `mlx` backend) is kept for one-off manual runs but isn't invoked by the queue when `[whisperx]` is in effect.

## Don't

- Don't redesign the per-video page. Edit `scripts/templates/video_template.html` instead.
- Don't invoke the `frontend-design` skill from the youtube-insights flow — the design is locked.
- Don't write outputs to `/tmp` while inside this project.
- Don't overwrite `notes.md` or `library/scores.json`.
- Don't truncate `history.jsonl` — append-only.
- Don't rename per-video folders away from the 11-char YouTube ID; `videos/<title-slug>` symlinks are fine and `build_indexes.py` skips symlinks automatically.
- Don't paste large transcripts into chat — the artifact is the deliverable.
- Don't skip the session-start tracker check unless there's truly nothing to surface.
- Don't commit `.venv/` (it's ~1.2 GB; covered by `.gitignore`).
- Don't manually invoke `scripts/transcribe_whisperx.py` with the system `python3` — it must run via `.venv/bin/python`.
