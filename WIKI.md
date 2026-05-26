# Pipeline — How a YouTube Video Becomes a Transcript with Speaker Labels

## Terms explained

**ASR (Automatic Speech Recognition)**
The core "speech → text" step. Feed it audio; it returns a transcript. It has no idea who said what — just what words were spoken and roughly when.

**Alignment**
ASR gives you rough timestamps per sentence. Alignment re-reads the audio word-by-word and pins each word to its exact millisecond. This is necessary so diarization can accurately assign "this word at 00:04:32.1 belongs to Speaker A."

**Diarization**
Answers: "who spoke when?" It listens for changes in voice characteristics and assigns labels like `SPEAKER_00`, `SPEAKER_01`. It doesn't know names — just that two voices are distinct people.

**LLM Analysis**
A large language model reads the finished transcript and extracts structured meaning: insights, entities, predictions, thesis. Separate from transcription entirely — comprehension, not listening.

---

## The 5-step pipeline

```
YouTube URL
    │
    ▼
[1] yt-dlp — fetch metadata (title, channel, duration, upload date)
    │
    ▼
[2] Transcription — two paths depending on [groq] or [whisperx] prefix in queue.txt:

    ── WhisperX path (default) ──────────────────────────────────────
    │  2a. yt-dlp downloads audio (.m4a)
    │  2b. ASR: Whisper large-v3 (local) → raw text + rough timestamps
    │  2c. Alignment: wav2vec2 model → word-level timestamps
    │  2d. Diarization: pyannote/speaker-diarization-3.1 (local) → SPEAKER_00, SPEAKER_01…
    │      → output: [00:01:23] SPEAKER_00: "The key insight here is…"
    │
    ── Groq path ([groq] prefix) ────────────────────────────────────
    │  2a. yt-dlp downloads audio (.m4a)
    │  2b. ASR: Whisper large-v3 via Groq API → text + timestamps
    │      → NO alignment, NO speaker labels
    │      → output: [00:01:23] "The key insight here is…"
    │
    ▼
[3] Analysis: claude-opus-4-7 reads transcript → insights, entities, predictions
    │
    ▼
[4] Render: build_video.py → index.html + insights.json / entities.json / predictions.json
    │
    ▼
[5] Post-process: append to history.jsonl, create readable symlink (videos/jensen-huang-…)
```

---

## Models at a glance

| Step | Model | Where it runs | Approx. time (60-min podcast) |
|---|---|---|---|
| ASR | Whisper large-v3 | Local CPU (WhisperX) or Groq API | ~22 min local / ~30s Groq |
| Alignment | wav2vec2 (built into WhisperX) | Local CPU | ~80s |
| Diarization | pyannote/speaker-diarization-3.1 | Local CPU | ~52 min ← bottleneck |
| Analysis | claude-opus-4-7 | Anthropic API | ~90s |

The ~75 min total for a 60-min podcast using WhisperX is almost entirely diarization on CPU. Groq skips alignment and diarization, finishing in ~30 seconds but producing no speaker labels.

---

## Choosing a backend

| Use `[whisperx]` when… | Use `[groq]` when… |
|---|---|
| Multi-speaker podcast — you want SPEAKER_00 / SPEAKER_01 labels | Solo narration or lecture |
| You can wait ~60–100 min | You need results in under a minute |
| Free compute cost matters | Groq free tier has headroom (8 hrs audio/day) |

Per-URL override syntax in `queue.txt`:

```
https://www.youtube.com/watch?v=...               # uses default (whisperx)
[groq]     https://www.youtube.com/watch?v=...    # force Groq — fast, no speakers
[whisperx] https://www.youtube.com/watch?v=...    # force WhisperX — slow, with speakers
```

See `instructions.md` for full processing commands and `GOTCHAS.md` for known traps.
