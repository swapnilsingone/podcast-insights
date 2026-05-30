# Setup

First-time setup for the two transcription backends and the analysis model. You only need to do this once per machine.

All secrets live in `../.env.local` (i.e. `claude-code/.env.local`, one directory above the project). The file is `chmod 600` and sourced automatically by `scripts/process_queue.sh`.

```
claude-code/
├── .env.local          ← lives here (mode 600)
└── podcast-insights/   ← this project
```

---

## 1. System tools (both backends)

```bash
brew install yt-dlp ffmpeg
```

Used by every video — `yt-dlp` fetches the audio + metadata, `ffmpeg` does any cutting/conversion.

---

## 2. Analysis model — Claude Code CLI

The pipeline locks the analysis step to `claude-opus-4-7`. You need the `claude` CLI on your PATH and authenticated.

```bash
claude --version           # any 1.x is fine
```

If it's missing, install per the Claude Code docs and sign in. The script will refuse to run if `claude` isn't on PATH.

---

## 3. Groq backend (fast, no speaker labels)

Used for any URL prefixed with `[groq]` in `queue.txt`, or for the whole queue if you flip `TRANSCRIPTION_DEFAULT=groq`.

1. Get a key at https://console.groq.com/keys
2. Add to `../.env.local`:

   ```
   GROQ_API_KEY=gsk_...
   ```

3. `chmod 600 ../.env.local`

That's it. Transcribes 1 hour of audio in ~30s. Free tier is 7,200 audio-seconds/day.

---

## 4. WhisperX backend (default; speaker diarization)

Used for any URL without a prefix (default), or with `[whisperx]`. Runs locally on CPU, ~60–100 min per hour of audio on an M-series Mac.

### 4.1 Create the project venv (Python 3.12)

We keep WhisperX in a dedicated venv so its torch / pyannote pin doesn't fight with the rest of the system Python.

```bash
# From the project root:
pyenv install -s 3.12.11
/Users/$USER/.pyenv/versions/3.12.11/bin/python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install whisperx
```

Expected: ~1.2 GB on disk. The repo's `.gitignore` excludes `.venv/`.

Verify:

```bash
.venv/bin/python -c "import whisperx, torch; from pyannote.audio import Pipeline; print('ok', torch.__version__)"
```

(You'll see harmless `libtorchcodec` / `libavutil` warnings on macOS — `transcribe_whisperx.py` silences them, and we never use torchcodec anyway.)

### 4.2 HuggingFace token (the part with three gotchas)

WhisperX downloads pyannote models from HuggingFace. They're gated, meaning **all three** of these must be true:

1. You are signed in to HuggingFace as account `X`.
2. Account `X` has clicked "Agree" on each gated model page (one-time, per model).
3. The token in `HF_TOKEN` belongs to account `X` and is of type **Read** (not fine-grained), OR is fine-grained with `Read access to contents of all public gated repos you can access` ticked.

If any one of those is off, you'll see `403 Forbidden` on `config.yaml` and the script marks the URL `# FAILED-TRANSCRIPTION:`.

**Steps:**

1. Sign in (or sign up) at https://huggingface.co — note the username (this is your account `X`).
2. While signed in as that account, click "Agree and access repository" on both:
   - https://huggingface.co/pyannote/segmentation-3.0
   - https://huggingface.co/pyannote/speaker-diarization-community-1
3. Generate a token at https://huggingface.co/settings/tokens → **"+ Create new token"** → choose **Read** (the simple option). Copy the `hf_...` string.
4. Add to `../.env.local`:

   ```
   HF_TOKEN=hf_...
   ```

5. `chmod 600 ../.env.local`

**Verify access (token can read the gated config):**

```bash
set -a && . ../.env.local && set +a
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $HF_TOKEN" \
  https://huggingface.co/pyannote/speaker-diarization-community-1/resolve/main/config.yaml
# Want: 200. Got 403? See troubleshooting below.
```

### 4.3 Troubleshooting 403s

| Symptom | Cause | Fix |
|---|---|---|
| `403` on `config.yaml`, page shows "Agree and access repository" | You haven't accepted the gate as the token's owner | Sign in as the token's HF user → click Agree |
| `403` on `config.yaml`, page shows "You have been granted access" | Token is fine-grained without the gated-repo permission | Make a new **Read** token, or edit the existing token and tick *Read access to contents of all public gated repos you can access* |
| `403`, but a different HF account is signed in | Browser session ≠ token owner | Sign out, sign in as the right account, then accept |

Find out who the token belongs to:

```bash
set -a && . ../.env.local && set +a
curl -s -H "Authorization: Bearer $HF_TOKEN" https://huggingface.co/api/whoami-v2 | python3 -m json.tool | head -5
```

### 4.4 Pyannote model cache

First diarization run downloads `pyannote/speaker-diarization-community-1` (~500 MB) into `~/.cache/huggingface/`. After that, subsequent runs reuse the cache and you only pay the inference cost.

---

## 5. Sanity-check the full pipeline

After secrets are in `../.env.local`:

```bash
# Preflight: no URLs needed, just confirms keys + claude CLI + .venv all resolve.
./scripts/process_queue.sh
# (Should print "Queue is empty — nothing to do.")
```

To actually process something, drop a URL in `queue.txt` and run again. Use a short video for the first WhisperX test — even a 10-min clip is a meaningful smoke test (~10–15 min wall time, dominated by diarization).

## 6. Watching realtime progress of Transcription and Diarization
.venv/bin/python -u scripts/transcribe_whisperx.py <id>