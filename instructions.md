# Podcast Insights — How-To Instructions

## Starting the local server

Library pages (`library/*.html`) use `fetch()` and silently fail over `file://`. Always serve them over HTTP.

**Start the server (idempotent — safe to run if already up):**

```bash
lsof -i :8765 >/dev/null 2>&1 || python3 scripts/serve.py &
```

Then open the library:

```bash
open http://localhost:8765/library/index.html
```

From inside a Claude Code session, prefix with `!` to run in-terminal:

```
! lsof -i :8765 >/dev/null 2>&1 || python3 scripts/serve.py &
```

The server logs to stdout in the background and stays up for the session. To stop it:

```bash
kill $(lsof -ti :8765)
```

**Library pages and what they read:**

- `index.html` — gallery (reads `library.json` + `insights.json`)
- `dashboard.html` — compounding tracker (reads `tracker.json`, `predictions.json`, `library.json`)
- `entities.html` / `predictions.html` / `speakers.html` / `qa.html` / `compare.html` / `highlights.html` — each reads its named JSON

## Processing a video manually

1. Add the URL to `queue.txt` (with optional `[groq]` or `[whisperx]` prefix)
2. Run the queue script:

```bash
./scripts/process_queue.sh
```

Or force a specific backend for the whole run:

```bash
TRANSCRIPTION_DEFAULT=groq ./scripts/process_queue.sh
```

## Rebuilding library indexes

After manually editing video data or adding new videos outside the queue script:

```bash
python3 scripts/build_indexes.py
```

This is idempotent — safe to run any time.

## WhisperX transcription timing (reference)

Measured on Jensen Huang × All-In (66-min audio, M-series CPU, int8):

| Step | Wall time | Notes |
|---|---|---|
| First-time model download (large-v3) | ~11 min | ~2.9 GB to `~/.cache/huggingface/`. Cached after. |
| ASR (large-v3 → 178 raw segments) | ~22 min | 3.0× realtime |
| Alignment (word-level timestamps) | ~80 s | negligible |
| Diarization (community-1) | ~52 min | 1.3× realtime; dominates total cost |
| Analysis + render + post-process | ~90 s | the rest of the pipeline |
| **Total (steady state)** | **~75 min** | for a 60-min podcast |
