# Podcast Insights

Personal archive of interactive video-insight artifacts. Each YouTube/podcast URL becomes a single-file HTML page with insight cards, charts, deep-links into the source video, and pull quotes.

## Add a video

Two ways:

**Inline.** In a Claude Code session opened in this folder, paste the URL and ask:

> process this: https://www.youtube.com/watch?v=...

**Batch.** Add URLs to `queue.txt` (one per line, comments with `#` allowed), then:

> process the queue

## See your library

```bash
python3 scripts/serve.py --open
```

This starts a local server on port 8765 and opens the dashboard. The library pages need to be served over HTTP because Chrome blocks `fetch()` between local `file://` paths.

(Per-video artifacts at `videos/<id>/index.html` work fine via `file://` — their data is inlined.)

## Per-video files

```
videos/<video_id>/
  index.html       ← the artifact (open this)
  insights.json    ← structured extraction (audit / reuse)
  notes.md         ← your own notes — never overwritten
  source/          ← raw .vtt, metadata, parsed transcript
```

## History

`history.jsonl` is an append-only log of every processing run. One JSON record per line.

## Setup

```bash
brew install yt-dlp ffmpeg
pip3 install -U mlx-whisper
```

Transcription defaults to **mlx-whisper large-v3** running locally on Apple Silicon — accurate punctuation, real proper nouns, free, ~real-time. Falls back to YouTube auto-captions if whisper isn't available.

To transcribe a specific video manually:
```bash
python3 scripts/transcribe.py <video_id> --model large-v3
```

Models available (`--model`): `tiny`, `base`, `small`, `medium`, `large-v3` (default), `large-v3-turbo`.
