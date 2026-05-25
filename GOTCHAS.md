# Gotchas

Non-obvious traps that have bitten this project. Skim before debugging anything that looks similar — most of the symptoms below already have a known explanation. Each entry has the symptom first so you can grep for it.

## `claude -p` SIGKILLs silently on large argv

**Symptom in pipeline output:** `claude failed (exit -9):` with no stderr text.

Any prompt over ~50 KB passed as a CLI argument to the `claude` CLI returns exit `-9` (SIGKILL). The same prompt via stdin works fine. The argv size is well under `ARG_MAX`, but the bundled Node runtime chokes on it.

`scripts/analyze.py` pipes the prompt via stdin (`subprocess.run(cmd, input=prompt, ...)`) — **preserve that pattern in any new script** that shells out to `claude`. WhisperX transcripts are typically 60–80 KB and trip this every time.

## HuggingFace 403 on `pyannote/*` config.yaml

**Symptom:** `HTTPError: 403 Forbidden` (or `GatedRepoError`) on `…/resolve/main/config.yaml` while loading the diarization pipeline.

Three independent causes — **all three** must be true for diarization to download:

1. The token's owner has clicked "Agree" on each gated pyannote model page.
2. The browser session you accepted the gates from belongs to that same owner. Gates accepted under a different HF account are useless.
3. The token type is **Read** (the classic kind), or **Fine-grained** with `Read access to contents of all public gated repos you can access` ticked.

To diagnose, check the token's owner:

```bash
set -a && . ../.env.local && set +a
curl -s -H "Authorization: Bearer $HF_TOKEN" https://huggingface.co/api/whoami-v2 \
  | python3 -m json.tool | head -5
```

Cross-reference that against the account whose gates you accepted in the browser. If they differ, you found the bug. See `SETUP.md` § 4.2 for the full setup walkthrough.

## WhisperX progress is invisible when piped

**Symptom:** Log file frozen at `[2/5] transcribe (whisperx, locked policy)` for tens of minutes; `tail -f` shows no movement.

`transcribe_whisperx.py`'s `print()` calls block-buffer when piped through `tee`/`sed`/redirects, so the log appears stalled for the entire ASR + diarize span (90+ min on a 1-hour podcast). The process is fine.

To verify health:

```bash
ps -p <pid> -o pcpu,rss,etime,time
# Expect %CPU > 100, RSS ~3 GB while large-v3 is loaded.
```

`process_queue.sh` already invokes the venv Python with `-u` (unbuffered), so progress should flush in real time. If you're invoking `transcribe_whisperx.py` directly without `-u`, the freeze is back.

## `pyenv install <version>` without `xz` on PATH → `_lzma` missing

**Symptom:** `ModuleNotFoundError: No module named '_lzma'` from somewhere inside the WhisperX import chain (often `torchvision` → `torchcodec` → `lzma`).

A Python built without `_lzma` will fail to import any package that touches `torchvision`/`torchcodec`/`lzma` transitively — and pyannote does.

Fix: rebuild the Python with xz on PATH, or use a different Python that has it.

```bash
brew install xz
LDFLAGS="-L$(brew --prefix xz)/lib" CPPFLAGS="-I$(brew --prefix xz)/include" \
  pyenv install <version>
```

The project's `.venv` uses 3.12.11 specifically because the local 3.11.13 was missing `_lzma`. **Don't switch the venv's base Python without first verifying `python -c "import lzma"` works.**

## `torchcodec` FFmpeg-dlopen warnings on macOS are spurious

**Symptom:** A wall of `Library not loaded: @rpath/libavutil.NN.dylib` warnings printed when `whisperx` is loaded.

Harmless. `torchcodec` is trying every FFmpeg ABI it knows about. WhisperX loads audio via its own `ffmpeg` subprocess, never `torchcodec`. `transcribe_whisperx.py` filters these warnings explicitly.

**Don't** try to "fix" by installing FFmpeg dev libraries or older `libavutil` versions — that's chasing a phantom dependency.
