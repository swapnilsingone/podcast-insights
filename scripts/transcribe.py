#!/usr/bin/env python3
"""Transcribe a YouTube video's audio to transcript.json + transcript.txt.

Backends (in priority order):
  groq   — Groq Whisper API (whisper-large-v3). Requires GROQ_API_KEY env var.
            Splits audio into <25 MB chunks, uploads each, merges timestamps.
            ~30s for a 1-hour file. Free tier: 7 200 audio-seconds/day.
  mlx    — mlx-whisper large-v3 on Apple Silicon (local, no API key needed).
            Requires model download from HuggingFace (~1.5 GB, one-time).

Usage:
  python3 scripts/transcribe.py <video_id_or_url>
  python3 scripts/transcribe.py <id> --backend mlx        # force local
  python3 scripts/transcribe.py <id> --backend groq       # force Groq
  python3 scripts/transcribe.py <id> --reuse-audio        # skip re-download
  python3 scripts/transcribe.py <id> --language en
  python3 scripts/transcribe.py <id> --output-dir <path>

Requires: yt-dlp, ffmpeg  (brew install yt-dlp ffmpeg)
Groq:     pip3 install groq
mlx:      pip3 install mlx-whisper
"""
import argparse, json, os, re, subprocess, sys, tempfile, time, shutil
from pathlib import Path

ROOT   = Path(__file__).resolve().parent.parent
VIDEOS = ROOT / "videos"

GROQ_MODEL = "whisper-large-v3"
CHUNK_S    = 1320   # 22 min — keeps chunks well under Groq's 25 MB limit

MLX_MODEL_REPOS = {
    "tiny":           "mlx-community/whisper-tiny-mlx",
    "base":           "mlx-community/whisper-base-mlx",
    "small":          "mlx-community/whisper-small-mlx",
    "medium":         "mlx-community/whisper-medium-mlx",
    "large":          "mlx-community/whisper-large-v3-mlx",
    "large-v3":       "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo":          "mlx-community/whisper-large-v3-turbo",
}


def need(cmd):
    if not shutil.which(cmd):
        sys.exit(f"ERROR: {cmd} not installed. Run: brew install yt-dlp ffmpeg")


def extract_video_id(arg):
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", arg)
    if m: return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", arg): return arg
    sys.exit(f"Couldn't extract video id from: {arg}")


def download_audio(video_id, out_dir, reuse=False):
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / f"{video_id}.m4a"
    if reuse and audio_path.exists():
        print(f"  reusing {audio_path.name}")
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


# ── Groq backend ──────────────────────────────────────────────────────────────

def _split_audio(audio_path, total_seconds, tmp_dir):
    """Split audio into CHUNK_S-second pieces for Groq's 25 MB limit."""
    chunks = []
    offset = 0
    i = 0
    while offset < total_seconds:
        out = Path(tmp_dir) / f"chunk_{i:02d}.m4a"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(audio_path),
            "-ss", str(offset), "-t", str(CHUNK_S), "-c", "copy", str(out),
        ], check=True, capture_output=True)
        size_mb = out.stat().st_size / 1024 / 1024
        print(f"  chunk {i}: {offset//60:.0f}m offset  {size_mb:.1f} MB", flush=True)
        chunks.append((out, offset))
        offset += CHUNK_S
        i += 1
    return chunks


def transcribe_groq(audio_path, language=None):
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        sys.exit("ERROR: GROQ_API_KEY env var not set. Export it or use --backend mlx.")
    try:
        from groq import Groq
    except ImportError:
        sys.exit("ERROR: groq not installed. Run: pip3 install groq")

    client = Groq(api_key=api_key)

    # Get audio duration
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    total_seconds = float(r.stdout.strip())
    print(f"  audio duration: {total_seconds/60:.1f} min", flush=True)

    all_segments = []
    t0 = time.time()

    with tempfile.TemporaryDirectory() as tmp:
        chunks = _split_audio(audio_path, total_seconds, tmp)
        for i, (chunk_path, offset) in enumerate(chunks):
            pct = offset / total_seconds * 100
            print(f"  transcribing chunk {i+1}/{len(chunks)}  [{offset//60:.0f}m offset]  {pct:.0f}% done...", flush=True)
            ct0 = time.time()
            kwargs = {"model": GROQ_MODEL, "response_format": "verbose_json",
                      "timestamp_granularities": ["segment"]}
            if language:
                kwargs["language"] = language
            with open(chunk_path, "rb") as f:
                resp = client.audio.transcriptions.create(file=(chunk_path.name, f), **kwargs)
            for seg in resp.segments:
                all_segments.append({
                    "start": seg["start"] + offset,
                    "end":   seg["end"]   + offset,
                    "text":  seg["text"].strip(),
                })
            print(f"    -> {len(resp.segments)} segments in {time.time()-ct0:.1f}s", flush=True)

    all_segments.sort(key=lambda s: s["start"])
    dt = time.time() - t0
    print(f"  groq done in {dt:.1f}s ({total_seconds/dt:.1f}× realtime)", flush=True)
    return all_segments, total_seconds


# ── mlx-whisper backend ───────────────────────────────────────────────────────

def transcribe_mlx(audio_path, model_key="large-v3", language=None):
    repo = MLX_MODEL_REPOS.get(model_key, MLX_MODEL_REPOS["large-v3"])
    print(f"  transcribing with {repo}...")
    try:
        import mlx_whisper
    except ImportError:
        sys.exit("ERROR: mlx-whisper not installed. Run: pip3 install mlx-whisper")
    t0 = time.time()
    kwargs = {"path_or_hf_repo": repo, "word_timestamps": True, "verbose": True}
    if language:
        kwargs["language"] = language
    result = mlx_whisper.transcribe(str(audio_path), **kwargs)
    dt = time.time() - t0
    segs = result.get("segments", [])
    audio_dur = segs[-1]["end"] if segs else 0
    rtf = (audio_dur / dt) if dt > 0 else 0
    print(f"  done in {dt:.1f}s (audio {audio_dur:.0f}s → {rtf:.1f}× realtime)")
    segments = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()} for s in segs]
    return segments, audio_dur


# ── Shared output helpers ─────────────────────────────────────────────────────

def chunk_segments(segments, target=30):
    chunks, cur_start, cur_text = [], None, []
    for seg in segments:
        if cur_start is None:
            cur_start = seg["start"]
        if seg["end"] - cur_start > target and cur_text:
            chunks.append({"t": cur_start, "text": " ".join(cur_text).strip()})
            cur_text, cur_start = [], seg["start"]
        cur_text.append(seg["text"])
    if cur_text:
        chunks.append({"t": cur_start or 0, "text": " ".join(cur_text).strip()})
    return chunks


def fmt_ts(t):
    t = int(t)
    return f"[{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}]"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="11-char YouTube ID or full URL")
    ap.add_argument("--backend", default=None, choices=["groq", "mlx"],
                    help="transcription backend (default: groq if GROQ_API_KEY set, else mlx)")
    ap.add_argument("--model", default="large-v3", choices=list(MLX_MODEL_REPOS.keys()),
                    help="mlx model variant (ignored for groq backend)")
    ap.add_argument("--language", default=None, help="ISO language code, e.g. en")
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--reuse-audio", action="store_true")
    args = ap.parse_args()

    need("yt-dlp"); need("ffmpeg")

    # Resolve backend
    backend = args.backend
    if backend is None:
        backend = "groq" if os.environ.get("GROQ_API_KEY") else "mlx"
    print(f"Backend: {backend}")

    vid     = extract_video_id(args.video)
    out_dir = Path(args.output_dir) if args.output_dir else (VIDEOS / vid / "source")

    print(f"Video: {vid}")
    audio = download_audio(vid, out_dir, reuse=args.reuse_audio)

    if backend == "groq":
        segments, audio_dur = transcribe_groq(audio, language=args.language)
        raw_path = out_dir / f"{vid}.groq.json"
    else:
        segments, audio_dur = transcribe_mlx(audio, model_key=args.model, language=args.language)
        raw_path = out_dir / f"{vid}.whisper.json"

    raw_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2))
    chunks = chunk_segments(segments, target=30)
    (out_dir / "transcript.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=2))
    (out_dir / "transcript.txt").write_text("\n\n".join(f"{fmt_ts(c['t'])} {c['text']}" for c in chunks))

    total_chars = sum(len(c["text"]) for c in chunks)
    print(f"\nWrote:")
    print(f"  {raw_path.relative_to(ROOT)}")
    print(f"  {(out_dir/'transcript.json').relative_to(ROOT)}  ({len(chunks)} chunks, {total_chars} chars)")
    print(f"  {(out_dir/'transcript.txt').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
