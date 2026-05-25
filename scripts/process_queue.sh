#!/usr/bin/env bash
# Process every URL in queue.txt end-to-end without a Claude Code session.
#
# Locked policy (see CLAUDE.md — do not change loosely):
#   - Analysis model: claude-opus-4-7  (no flags, no aliases, no env overrides)
#   - Default transcription backend: whisperx  (local; speaker diarization on)
#       Override per URL in queue.txt with a prefix:
#         [groq]     https://www.youtube.com/watch?v=...   # fast, no speakers
#         [whisperx] https://www.youtube.com/watch?v=...   # explicit (default)
#       Or flip the default for the whole run via env:
#         TRANSCRIPTION_DEFAULT=groq ./scripts/process_queue.sh
#
# Failure markers in queue.txt (URL line is rewritten, preserving any [backend] prefix):
#   # FAILED-METADATA:      yt-dlp couldn't fetch info
#   # FAILED-TRANSCRIPTION: transcription step failed (key/auth/api/quota)
#   # FAILED-MODEL:         claude -p (opus 4.7) analysis failed
#   # FAILED-MODEL+TRANSCRIPTION: both failed in preflight
#
# Successful URLs are removed from queue.txt.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# Load .env.local from the parent (claude-code/) directory if present.
ENV_FILE="$(cd "$PROJECT_DIR/.." && pwd)/.env.local"
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
fi

# ── Locked policy ────────────────────────────────────────────────────────────
REQUIRED_MODEL="claude-opus-4-7"
export CLAUDE_MODEL="$REQUIRED_MODEL"

# Default transcription backend. Override via TRANSCRIPTION_DEFAULT=groq, or
# per-URL with a `[groq]` / `[whisperx]` prefix in queue.txt.
TRANSCRIPTION_DEFAULT="${TRANSCRIPTION_DEFAULT:-whisperx}"
case "$TRANSCRIPTION_DEFAULT" in
  groq|whisperx) : ;;
  *) echo "✗ TRANSCRIPTION_DEFAULT must be 'groq' or 'whisperx' (got: $TRANSCRIPTION_DEFAULT)" >&2; exit 2 ;;
esac

if [ "$#" -gt 0 ]; then
  echo "✗ process_queue.sh takes no arguments (policy locked in CLAUDE.md;" >&2
  echo "  flip the default with TRANSCRIPTION_DEFAULT=... or per-URL prefixes)." >&2
  echo "  Got: $*" >&2
  exit 2
fi

QUEUE="$PROJECT_DIR/queue.txt"
[ -f "$QUEUE" ] || { echo "No queue.txt at $QUEUE" >&2; exit 1; }

# Pull live, non-comment, non-empty lines (preserving any [backend] prefix).
RAW_LINES=$(grep -vE '^\s*(#|$)' "$QUEUE" || true)
if [ -z "$RAW_LINES" ]; then
  echo "Queue is empty — nothing to do."; exit 0
fi
LINE_COUNT=$(printf '%s\n' "$RAW_LINES" | wc -l | tr -d ' ')

# ── Per-line parsing helpers ─────────────────────────────────────────────────
parse_backend() {
  # Echo "groq" or "whisperx" if the line is prefixed; else empty.
  echo "$1" | grep -oE '^\[(groq|whisperx)\]' | tr -d '[]' || true
}

strip_prefix() {
  echo "$1" | sed -E 's/^\[(groq|whisperx)\][[:space:]]+//'
}

extract_id() {
  echo "$1" | grep -oE '[a-zA-Z0-9_-]{11}' | head -1
}

# Resolve which backend each line uses (prefix override or default).
backend_for() {
  local LINE="$1"
  local B
  B="$(parse_backend "$LINE")"
  echo "${B:-$TRANSCRIPTION_DEFAULT}"
}

# Tally which backends are needed across the queue (for preflight scoping).
NEEDS_GROQ=false
NEEDS_WHISPERX=false
while IFS= read -r LINE; do
  [ -z "$LINE" ] && continue
  B="$(backend_for "$LINE")"
  [ "$B" = "groq" ]     && NEEDS_GROQ=true
  [ "$B" = "whisperx" ] && NEEDS_WHISPERX=true
done <<< "$RAW_LINES"

echo "▶ Processing $LINE_COUNT URL(s)"
echo "  model: $REQUIRED_MODEL"
echo "  default backend: $TRANSCRIPTION_DEFAULT (override per URL with [groq]/[whisperx])"
$NEEDS_GROQ     && echo "  some URLs need: groq"
$NEEDS_WHISPERX && echo "  some URLs need: whisperx"
echo

# ── Preflight ───────────────────────────────────────────────────────────────
preflight_groq_ok()     { [ -n "${GROQ_API_KEY:-}" ]; }
preflight_whisperx_ok() {
  [ -x "$PROJECT_DIR/.venv/bin/python" ] && [ -n "${HF_TOKEN:-}" ]
}
preflight_model_ok()    { command -v claude >/dev/null 2>&1; }

PREFLIGHT_TRANS_OK=true
PREFLIGHT_MODEL_OK=true

if $NEEDS_GROQ && ! preflight_groq_ok; then
  echo "  ✗ preflight: GROQ_API_KEY is not set (required for any [groq] URL)."
  PREFLIGHT_TRANS_OK=false
fi
if $NEEDS_WHISPERX && ! preflight_whisperx_ok; then
  if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
    echo "  ✗ preflight: .venv/bin/python missing (required for whisperx). See SETUP.md."
  fi
  if [ -z "${HF_TOKEN:-}" ]; then
    echo "  ✗ preflight: HF_TOKEN is not set (required for whisperx diarization). See SETUP.md."
  fi
  PREFLIGHT_TRANS_OK=false
fi
if ! preflight_model_ok; then
  echo "  ✗ preflight: 'claude' CLI not found on PATH (required for $REQUIRED_MODEL)."
  PREFLIGHT_MODEL_OK=false
fi

# Helpers that rewrite queue.txt against the FULL line (including any [prefix]).
mark_failed() {
  local FULL="$1"; local KIND="$2"
  awk -v target="$FULL" -v kind="$KIND" \
    '{ if ($0 == target) print "# FAILED-" kind ": " $0; else print }' \
    "$QUEUE" > "$QUEUE.tmp" && mv "$QUEUE.tmp" "$QUEUE"
}
dequeue() {
  local FULL="$1"
  awk -v target="$FULL" '$0 != target' "$QUEUE" > "$QUEUE.tmp" && mv "$QUEUE.tmp" "$QUEUE"
}

# Preflight short-circuit: if anything required is missing, mark all and bail.
if ! $PREFLIGHT_TRANS_OK || ! $PREFLIGHT_MODEL_OK; then
  KIND=""
  if ! $PREFLIGHT_TRANS_OK && ! $PREFLIGHT_MODEL_OK; then
    KIND="MODEL+TRANSCRIPTION"
  elif ! $PREFLIGHT_TRANS_OK; then
    KIND="TRANSCRIPTION"
  else
    KIND="MODEL"
  fi
  echo
  echo "  Preflight failed ($KIND). Marking all URLs and exiting."
  while IFS= read -r LINE; do
    [ -z "$LINE" ] && continue
    mark_failed "$LINE" "$KIND"
    echo "  # FAILED-$KIND: $LINE"
  done <<< "$RAW_LINES"
  echo
  echo "═══ Summary ═══"
  echo "  ✓ 0 succeeded"
  echo "  ✗ $LINE_COUNT failed (preflight)"
  exit 1
fi

# ── Per-URL pipeline ─────────────────────────────────────────────────────────
# Returns 0 on success. On failure echoes a FAIL=KIND tag line for the caller.
process_one() {
  local FULL="$1"
  local URL BACKEND VID
  URL="$(strip_prefix "$FULL")"
  BACKEND="$(backend_for "$FULL")"
  VID="$(extract_id "$URL")"

  if [ -z "$VID" ]; then
    echo "  ✗ Could not extract 11-char video ID from: $URL"
    echo "FAIL=METADATA"
    return 1
  fi

  local VDIR="$PROJECT_DIR/videos/$VID"
  echo "  ┌─ $VID  [backend=$BACKEND]  ($URL)"

  if [ -f "$VDIR/index.html" ] && [ -f "$VDIR/insights.json" ]; then
    echo "  │  already processed — skipping"
    echo "  └─"
    return 0
  fi

  mkdir -p "$VDIR/source"

  # 1. Metadata via yt-dlp.
  if [ ! -f "$VDIR/source/$VID.info.json" ]; then
    echo "  │  [1/5] yt-dlp metadata"
    if ! ( cd "$VDIR/source" && yt-dlp --skip-download --write-info-json -o "%(id)s.%(ext)s" "$URL" >/dev/null 2>&1 ); then
      echo "  │  ✗ yt-dlp metadata failed"
      echo "  └─"
      echo "FAIL=METADATA"
      return 1
    fi
  else
    echo "  │  [1/5] metadata already present"
  fi

  # 2. Transcription — backend per [prefix] or default.
  if [ ! -f "$VDIR/source/transcript.txt" ]; then
    echo "  │  [2/5] transcribe ($BACKEND, locked policy)"
    if [ "$BACKEND" = "groq" ]; then
      if ! python3 "$PROJECT_DIR/scripts/transcribe.py" "$VID" \
             --backend groq --reuse-audio --language en 2>&1 | sed 's/^/  │       /'; then
        echo "  │  ✗ groq transcription failed"
        echo "  └─"; echo "FAIL=TRANSCRIPTION"; return 1
      fi
    else
      # whisperx (runs in project venv, requires HF_TOKEN for diarization)
      if ! "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/transcribe_whisperx.py" "$VID" \
             --reuse-audio --language en --require-diarize 2>&1 | sed 's/^/  │       /'; then
        echo "  │  ✗ whisperx transcription failed"
        echo "  └─"; echo "FAIL=TRANSCRIPTION"; return 1
      fi
    fi
    if [ ! -s "$VDIR/source/transcript.txt" ]; then
      echo "  │  ✗ $BACKEND transcription produced no transcript.txt"
      echo "  └─"; echo "FAIL=TRANSCRIPTION"; return 1
    fi
  else
    echo "  │  [2/5] transcript already present"
  fi

  # 3. LLM analysis — opus 4.7 ONLY, no fallback.
  echo "  │  [3/5] analyze (claude -p, model=$REQUIRED_MODEL, locked)"
  if ! python3 "$PROJECT_DIR/scripts/analyze.py" "$VID" --model "$REQUIRED_MODEL" 2>&1 \
       | sed 's/^/  │       /'; then
    echo "  │  ✗ $REQUIRED_MODEL analysis failed"
    echo "  └─"; echo "FAIL=MODEL"; return 1
  fi

  # 4. Render HTML + sidecar JSON files.
  echo "  │  [4/5] render"
  if ! python3 "$PROJECT_DIR/scripts/build_video.py" "$VID" 2>&1 | sed 's/^/  │       /'; then
    echo "  │  ✗ build failed"
    echo "  └─"; echo "FAIL=MODEL"; return 1
  fi

  # 5. Post-process: history.jsonl, readable symlink.
  echo "  │  [5/5] post-process"
  PROJECT_DIR="$PROJECT_DIR" python3 - "$VID" <<'PY' 2>&1 | sed 's/^/  │       /'
import json, datetime, re, sys, os
from pathlib import Path
root = Path(os.environ.get('PROJECT_DIR', '.'))
vid = sys.argv[1]
vdir = root / "videos" / vid
ins = json.loads((vdir / "insights.json").read_text())
v = ins["video"]
entry = {
    "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "video_id": vid,
    "url": v.get("url"),
    "title": v.get("title"),
    "channel": v.get("channel"),
    "duration_seconds": v.get("duration_seconds"),
    "insight_count": len(ins.get("insights", [])),
    "thesis": ins.get("thesis"),
}
with open(root / "history.jsonl", "a") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
slug = re.sub(r'[^a-z0-9]+', '-', (v.get("title") or vid).lower()).strip('-')[:60]
if slug:
    link = root / "videos" / slug
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        os.symlink(vid, link)
        print(f"symlink: videos/{slug} -> {vid}")
    except OSError as e:
        print(f"symlink skipped ({e})")
print(f"history appended: {entry['insight_count']} insights")
PY

  echo "  └─ ✓ done"
  return 0
}

FAILED=()
SUCCEEDED=()
while IFS= read -r LINE; do
  [ -z "$LINE" ] && continue
  TMP_OUT="$(mktemp)"
  if PROJECT_DIR="$PROJECT_DIR" process_one "$LINE" 2>&1 | tee "$TMP_OUT"; then
    SUCCEEDED+=("$LINE")
    dequeue "$LINE"
  else
    FAILED+=("$LINE")
    KIND=$(grep -oE '^FAIL=(METADATA|TRANSCRIPTION|MODEL)$' "$TMP_OUT" | tail -1 | cut -d= -f2)
    [ -z "$KIND" ] && KIND="MODEL"
    mark_failed "$LINE" "$KIND"
    echo "  → marked queue.txt: # FAILED-$KIND: $LINE"
  fi
  rm -f "$TMP_OUT"
  echo
done <<< "$RAW_LINES"

# Rebuild library indexes once all videos are processed.
if [ ${#SUCCEEDED[@]} -gt 0 ]; then
  echo "▶ Rebuilding library indexes"
  python3 "$PROJECT_DIR/scripts/build_indexes.py" 2>&1 | sed 's/^/  /'
fi

echo
echo "═══ Summary ═══"
echo "  ✓ ${#SUCCEEDED[@]} succeeded"
echo "  ✗ ${#FAILED[@]} failed"
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "  failed URLs are marked '# FAILED-<KIND>:' in queue.txt — fix and uncomment to retry"
fi
