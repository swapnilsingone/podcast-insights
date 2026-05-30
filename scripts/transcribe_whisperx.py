#!/usr/bin/env python3
"""Transcribe a video with WhisperX (local) + pyannote diarization.

This is an alternative to scripts/transcribe.py's `groq` backend, used when you
want speaker labels in the transcript. It must run inside the project venv:

    .venv/bin/python scripts/transcribe_whisperx.py <video_id> [--reuse-audio]

Output (under videos/<id>/source/):
  transcript.txt   — `[hh:mm:ss] SPEAKER_xx: text` blocks (matches groq layout
                     but adds speaker prefix when diarization succeeds).
  transcript.json  — list of {"t": float, "text": str, "speaker": str|null}
  <id>.whisperx.json — raw segments with word-level timestamps & speakers

Diarization requires:
  1. `HF_TOKEN` env var (from ../.env.local).
  2. Accepting the model agreements once on huggingface.co:
       https://huggingface.co/pyannote/speaker-diarization-3.1
       https://huggingface.co/pyannote/segmentation-3.0
If `HF_TOKEN` is missing the script transcribes + aligns without diarization
(no speaker labels) and writes a clear warning to stderr. Fail-fast can be
enabled with `--require-diarize`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEOS = ROOT / "videos"

# Silence the torchcodec FFmpeg dlopen warnings — we never use torchcodec
# (whisperx loads audio via its own ffmpeg subprocess).
warnings.filterwarnings("ignore", message=".*libtorchcodec.*")
warnings.filterwarnings("ignore", message=".*torchcodec.*")
os.environ.setdefault("PYTORCH_DISABLE_TORCHCODEC", "1")


def extract_video_id(arg: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", arg)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", arg):
        return arg
    sys.exit(f"Couldn't extract video id from: {arg}")


def download_audio(video_id: str, out_dir: Path, reuse: bool = False) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / f"{video_id}.m4a"
    if reuse and audio_path.exists():
        print(f"  reusing {audio_path.name} ({audio_path.stat().st_size/1024/1024:.1f} MB)")
        return audio_path
    url = f"https://www.youtube.com/watch?v={video_id}"
    print("  downloading audio (bestaudio m4a)...")
    subprocess.run([
        "yt-dlp", "-f", "bestaudio[ext=m4a]/bestaudio",
        "--extract-audio", "--audio-format", "m4a", "--audio-quality", "0",
        "-o", str(out_dir / f"{video_id}.%(ext)s"), url,
    ], check=True)
    if not audio_path.exists():
        sys.exit(f"audio file missing; found: {list(out_dir.glob(f'{video_id}.*'))}")
    print(f"  audio: {audio_path.name} ({audio_path.stat().st_size/1024/1024:.1f} MB)")
    return audio_path


def fmt_ts(t: float) -> str:
    t = int(t)
    return f"[{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}]"


def chunk_with_speakers(segments: list[dict], target: float = 30.0) -> list[dict]:
    """Break segments into ~`target`-second chunks, never crossing speaker changes."""
    chunks: list[dict] = []
    cur_start = None
    cur_speaker = None
    cur_text: list[str] = []
    for seg in segments:
        sp = seg.get("speaker")
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if cur_start is None:
            cur_start, cur_speaker = seg["start"], sp
        # Flush if duration exceeded OR speaker changed.
        if cur_text and (
            seg["end"] - cur_start > target
            or (sp is not None and cur_speaker is not None and sp != cur_speaker)
        ):
            chunks.append({"t": cur_start, "speaker": cur_speaker, "text": " ".join(cur_text).strip()})
            cur_text, cur_start, cur_speaker = [], seg["start"], sp
        cur_text.append(text)
    if cur_text:
        chunks.append({"t": cur_start or 0.0, "speaker": cur_speaker, "text": " ".join(cur_text).strip()})
    return chunks


def _heartbeat(label: str, audio_dur: float, stop_event: threading.Event, interval: float = 30.0):
    """Print elapsed wallclock + moving × realtime every `interval` seconds.

    Used to make opaque pyannote/whisperx phases observable in piped logs (where
    tqdm bars are silenced). Runs on a daemon thread; stop via stop_event.set().
    """
    t0 = time.time()
    while not stop_event.wait(interval):
        elapsed = time.time() - t0
        rt = audio_dur / elapsed if elapsed > 0 else 0.0
        print(
            f"    [{label}] {elapsed:.0f}s elapsed · {rt:.2f}× realtime so far",
            file=sys.stderr, flush=True,
        )


# ── WhisperX pipeline ────────────────────────────────────────────────────────

def transcribe_whisperx(
    audio_path: Path,
    language: str = "en",
    model_name: str = "large-v3",
    diarize: bool = True,
    hf_token: str | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> tuple[list[dict], float]:
    import whisperx

    device = "cpu"          # ctranslate2 doesn't support MPS; CPU + int8 is the realistic path
    compute_type = "int8"
    batch_size = 8

    print(f"  loading whisper model={model_name} (device={device}, compute_type={compute_type})...")
    t0 = time.time()
    model = whisperx.load_model(model_name, device=device, compute_type=compute_type, language=language)
    print(f"    loaded in {time.time()-t0:.1f}s")

    print("  loading audio...")
    audio = whisperx.load_audio(str(audio_path))
    audio_dur = len(audio) / 16000.0

    print(f"  transcribing ({audio_dur/60:.1f} min)...")
    t0 = time.time()
    # print_progress=True → faster-whisper emits per-chunk progress to stderr,
    # visible even when piped (e.g., through process_queue.sh's sed wrapper).
    result = model.transcribe(audio, batch_size=batch_size, print_progress=True)
    dt = time.time() - t0
    print(f"    transcribed in {dt:.1f}s ({audio_dur/dt:.1f}× realtime)")

    # Free ASR model memory before loading alignment + diarization models.
    del model

    # Word-level alignment (required for accurate speaker assignment).
    print(f"  loading alignment model for lang={language}...")
    t0 = time.time()
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    print(f"    aligning ({len(result['segments'])} segments)...")
    result = whisperx.align(
        result["segments"], align_model, metadata, audio, device,
        return_char_alignments=False,
    )
    print(f"    aligned in {time.time()-t0:.1f}s")
    del align_model

    # Diarization (optional).
    if diarize:
        if not hf_token:
            print("  ! HF_TOKEN not set — skipping diarization (no speaker labels).", file=sys.stderr)
        else:
            print("  loading diarization pipeline (pyannote)...")
            t0 = time.time()
            from whisperx.diarize import DiarizationPipeline
            diarize_pipeline = DiarizationPipeline(token=hf_token, device=device)
            kwargs = {}
            if min_speakers is not None:
                kwargs["min_speakers"] = min_speakers
            if max_speakers is not None:
                kwargs["max_speakers"] = max_speakers
            print(f"    diarizing ({audio_dur/60:.1f} min)...")
            # Heartbeat: pyannote's pipeline call is opaque (no progress events),
            # so a 30s-tick thread prints elapsed + observed × realtime to stderr
            # — early warning if you're on the slow track (0.1×) vs fast (0.7×).
            stop = threading.Event()
            hb = threading.Thread(
                target=_heartbeat, args=("diarize", audio_dur, stop),
                daemon=True,
            )
            hb.start()
            try:
                diarize_df = diarize_pipeline(audio, **kwargs)
            finally:
                stop.set()
                hb.join(timeout=1)
            dt = time.time() - t0
            print(f"    diarized in {dt:.1f}s ({audio_dur/dt:.1f}× realtime)")
            result = whisperx.assign_word_speakers(diarize_df, result)
            del diarize_pipeline

    # Normalise to our segment shape: {start, end, text, speaker?}
    segments: list[dict] = []
    for s in result.get("segments", []):
        seg = {
            "start": float(s.get("start", 0.0)),
            "end": float(s.get("end", 0.0)),
            "text": (s.get("text") or "").strip(),
        }
        if "speaker" in s and s["speaker"]:
            seg["speaker"] = s["speaker"]
        segments.append(seg)

    return segments, audio_dur


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="11-char YouTube ID or full URL")
    ap.add_argument("--language", default="en")
    ap.add_argument("--model", default="large-v3",
                    help="whisper model (tiny|base|small|medium|large-v3). Default: large-v3.")
    ap.add_argument("--reuse-audio", action="store_true",
                    help="don't re-download if <id>.m4a already exists")
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--no-diarize", action="store_true", help="skip pyannote speaker labelling")
    ap.add_argument("--require-diarize", action="store_true",
                    help="fail if diarization can't run (no HF_TOKEN, etc.)")
    ap.add_argument("--min-speakers", type=int, default=None)
    ap.add_argument("--max-speakers", type=int, default=None)
    ap.add_argument("--hf-token", default=None,
                    help="HuggingFace token (default: $HF_TOKEN env var)")
    args = ap.parse_args()

    for cmd in ("yt-dlp", "ffmpeg"):
        if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
            sys.exit(f"ERROR: {cmd} not installed. Run: brew install yt-dlp ffmpeg")

    vid = extract_video_id(args.video)
    out_dir = Path(args.output_dir) if args.output_dir else (VIDEOS / vid / "source")
    print(f"Video: {vid}")
    audio = download_audio(vid, out_dir, reuse=args.reuse_audio)

    hf_token = args.hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    diarize = not args.no_diarize
    if diarize and not hf_token and args.require_diarize:
        sys.exit(
            "ERROR: --require-diarize set but no HF_TOKEN found.\n"
            "  1) Get a token at https://huggingface.co/settings/tokens (read access is enough)\n"
            "  2) Accept terms at https://huggingface.co/pyannote/speaker-diarization-3.1\n"
            "                  and https://huggingface.co/pyannote/segmentation-3.0\n"
            "  3) Add HF_TOKEN=hf_xxx to ../.env.local"
        )

    segments, audio_dur = transcribe_whisperx(
        audio,
        language=args.language,
        model_name=args.model,
        diarize=diarize,
        hf_token=hf_token,
        min_speakers=args.min_speakers,
        max_speakers=args.max_speakers,
    )

    raw_path = out_dir / f"{vid}.whisperx.json"
    raw_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2))

    chunks = chunk_with_speakers(segments, target=30.0)
    (out_dir / "transcript.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=2))

    def line(c):
        prefix = f"{c['speaker']}: " if c.get("speaker") else ""
        return f"{fmt_ts(c['t'])} {prefix}{c['text']}"
    (out_dir / "transcript.txt").write_text("\n\n".join(line(c) for c in chunks))

    total_chars = sum(len(c["text"]) for c in chunks)
    speakers = sorted({c["speaker"] for c in chunks if c.get("speaker")})
    print()
    print("Wrote:")
    print(f"  {raw_path.relative_to(ROOT)}")
    print(f"  {(out_dir / 'transcript.json').relative_to(ROOT)}  ({len(chunks)} chunks, {total_chars} chars)")
    print(f"  {(out_dir / 'transcript.txt').relative_to(ROOT)}")
    if speakers:
        print(f"  speakers detected: {len(speakers)}  ({', '.join(speakers)})")
    else:
        print("  speakers: (none — diarization off or no HF_TOKEN)")


if __name__ == "__main__":
    main()
