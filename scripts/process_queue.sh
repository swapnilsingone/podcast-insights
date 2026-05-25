#!/usr/bin/env bash
# Process every URL in queue.txt end-to-end without a Claude Code session.
#
# Locked policy (see CLAUDE.md — do not override):
#   - Model:         claude-opus-4-7  (no flags, no aliases, no env overrides)
#   - Transcription: Groq only (whisper-large-v3). No mlx, no caption fallback.
#
# If either step fails, the URL stays in queue.txt with a marker indicating which
# part failed so you know what to fix:
#   # FAILED-METADATA:            yt-dlp metadata fetch failed
#   # FAILED-TRANSCRIPTION:       Groq transcription failed (key/api/quota)
#   # FAILED-MODEL:               claude -p (opus 4.7) analysis failed
#   # FAILED-MODEL+TRANSCRIPTION: both failed (preflight: no key AND no auth)
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
# Per CLAUDE.md: model is opus 4.7, transcription is Groq. Hardcoded here on
# purpose — do not add a CLI flag, alias, or env override. If you need a
# different model or backend, change CLAUDE.md first, then this file.
REQUIRED_MODEL="claude-opus-4-7"
export CLAUDE_MODEL="$REQUIRED_MODEL"

if [ "$#" -gt 0 ]; then
  echo "✗ process_queue.sh takes no arguments (policy is locked in CLAUDE.md)." >&2
  echo "  Got: $*" >&2
  exit 2
fi

QUEUE="$PROJECT_DIR/queue.txt"
if [ ! -f "$QUEUE" ]; then
  echo "No queue.txt at $QUEUE" >&2; exit 1
fi

# Pull live, non-comment, non-empty URLs.
URLS=$(grep -vE '^\s*(#|$)' "$QUEUE" || true)
if [ -z "$URLS" ]; then
  echo "Queue is empty — nothing to do."; exit 0
fi

URL_COUNT=$(printf '%s\n' "$URLS" | wc -l | tr -d ' ')
echo "▶ Processing $URL_COUNT URL(s)  (model: $REQUIRED_MODEL, transcription: groq)"
echo

# ── Preflight: Groq key + claude CLI ─────────────────────────────────────────
# Detect ahead of time so we can mark the URL with the right failure marker
# without burning a yt-dlp download.

preflight_transcription_ok() {
  [ -n "${GROQ_API_KEY:-}" ]
}

preflight_model_ok() {
  command -v claude >/dev/null 2>&1
}

PREFLIGHT_TRANS_OK=true
PREFLIGHT_MODEL_OK=true
preflight_transcription_ok || PREFLIGHT_TRANS_OK=false
preflight_model_ok          || PREFLIGHT_MODEL_OK=false

if ! $PREFLIGHT_TRANS_OK; then
  echo "  ✗ preflight: GROQ_API_KEY is not set (required; no fallback)."
fi
if ! $PREFLIGHT_MODEL_OK; then
  echo "  ✗ preflight: 'claude' CLI not found on PATH (required for $REQUIRED_MODEL)."
fi

# Helper: rewrite a queue line as a failure marker (preserves the URL after the colon).
mark_failed() {
  local URL="$1"; local KIND="$2"  # KIND: METADATA|TRANSCRIPTION|MODEL|MODEL+TRANSCRIPTION
  awk -v target="$URL" -v kind="$KIND" \
    '{ if ($0 == target) print "# FAILED-" kind ": " $0; else print }' \
    "$QUEUE" > "$QUEUE.tmp" && mv "$QUEUE.tmp" "$QUEUE"
}

dequeue() {
  local URL="$1"
  awk -v target="$URL" '$0 != target' "$QUEUE" > "$QUEUE.tmp" && mv "$QUEUE.tmp" "$QUEUE"
}

extract_id() {
  echo "$1" | grep -oE '[a-zA-Z0-9_-]{11}' | head -1
}

# If preflight has both failures, short-circuit the whole queue — every URL gets
# the combined marker and we exit without touching the network.
if ! $PREFLIGHT_TRANS_OK && ! $PREFLIGHT_MODEL_OK; then
  echo
  echo "  Both preflight checks failed. Marking all URLs and exiting."
  while IFS= read -r URL; do
    [ -z "$URL" ] && continue
    mark_failed "$URL" "MODEL+TRANSCRIPTION"
    echo "  # FAILED-MODEL+TRANSCRIPTION: $URL"
  done <<< "$URLS"
  echo
  echo "═══ Summary ═══"
  echo "  ✓ 0 succeeded"
  echo "  ✗ $URL_COUNT failed (preflight)"
  exit 1
fi

# If only one preflight failed, mark every URL with that single failure and exit.
# (Rationale: we can't satisfy the locked policy until the user fixes it.)
if ! $PREFLIGHT_TRANS_OK || ! $PREFLIGHT_MODEL_OK; then
  KIND=""
  $PREFLIGHT_TRANS_OK || KIND="TRANSCRIPTION"
  $PREFLIGHT_MODEL_OK || KIND="MODEL"
  echo
  echo "  Preflight failed ($KIND). Marking all URLs and exiting."
  while IFS= read -r URL; do
    [ -z "$URL" ] && continue
    mark_failed "$URL" "$KIND"
    echo "  # FAILED-$KIND: $URL"
  done <<< "$URLS"
  echo
  echo "═══ Summary ═══"
  echo "  ✓ 0 succeeded"
  echo "  ✗ $URL_COUNT failed (preflight)"
  exit 1
fi

# ── Per-URL pipeline ─────────────────────────────────────────────────────────
# Returns 0 on success, non-zero on failure. On failure, echoes one of:
#   FAIL=METADATA | FAIL=TRANSCRIPTION | FAIL=MODEL
# as the last line so the caller can pick the right queue marker.
process_one() {
  local URL="$1"
  local VID
  VID="$(extract_id "$URL")"
  if [ -z "$VID" ]; then
    echo "  ✗ Could not extract 11-char video ID from $URL"
    echo "FAIL=METADATA"
    return 1
  fi

  local VDIR="$PROJECT_DIR/videos/$VID"
  echo "  ┌─ $VID ($URL)"

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

  # 2. Transcription — GROQ ONLY, no fallback.
  if [ ! -f "$VDIR/source/transcript.txt" ]; then
    echo "  │  [2/5] transcribe (groq, locked)"
    if ! python3 "$PROJECT_DIR/scripts/transcribe.py" "$VID" \
           --backend groq --reuse-audio --language en 2>&1 | sed 's/^/  │       /'; then
      echo "  │  ✗ groq transcription failed"
      echo "  └─"
      echo "FAIL=TRANSCRIPTION"
      return 1
    fi
    if [ ! -s "$VDIR/source/transcript.txt" ]; then
      echo "  │  ✗ groq transcription produced no transcript.txt"
      echo "  └─"
      echo "FAIL=TRANSCRIPTION"
      return 1
    fi
  else
    echo "  │  [2/5] transcript already present"
  fi

  # 3. LLM analysis — opus 4.7 ONLY, no fallback.
  echo "  │  [3/5] analyze (claude -p, model=$REQUIRED_MODEL, locked)"
  if ! python3 "$PROJECT_DIR/scripts/analyze.py" "$VID" --model "$REQUIRED_MODEL" 2>&1 \
       | sed 's/^/  │       /'; then
    echo "  │  ✗ $REQUIRED_MODEL analysis failed"
    echo "  └─"
    echo "FAIL=MODEL"
    return 1
  fi

  # 4. Render HTML + sidecar JSON files.
  echo "  │  [4/5] render"
  if ! python3 "$PROJECT_DIR/scripts/build_video.py" "$VID" 2>&1 | sed 's/^/  │       /'; then
    echo "  │  ✗ build failed"
    echo "  └─"
    echo "FAIL=MODEL"  # build failure follows from a bad analysis.json
    return 1
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
while IFS= read -r URL; do
  [ -z "$URL" ] && continue
  # Capture combined output; the FAIL=KIND line (if present) is the last token.
  TMP_OUT="$(mktemp)"
  if PROJECT_DIR="$PROJECT_DIR" process_one "$URL" 2>&1 | tee "$TMP_OUT"; then
    SUCCEEDED+=("$URL")
    dequeue "$URL"
  else
    FAILED+=("$URL")
    KIND=$(grep -oE '^FAIL=(METADATA|TRANSCRIPTION|MODEL)$' "$TMP_OUT" | tail -1 | cut -d= -f2)
    [ -z "$KIND" ] && KIND="MODEL"  # default categorization if step didn't emit a tag
    mark_failed "$URL" "$KIND"
    echo "  → marked queue.txt: # FAILED-$KIND: $URL"
  fi
  rm -f "$TMP_OUT"
  echo
done <<< "$URLS"

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
