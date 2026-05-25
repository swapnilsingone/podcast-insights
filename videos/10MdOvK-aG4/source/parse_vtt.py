#!/usr/bin/env python3
"""Parse a yt-dlp .vtt into clean (start_seconds, text) chunks + a flat text file."""
import re, json
from pathlib import Path

HERE = Path(__file__).parent
VID = HERE.parent.name
vtt = (HERE / f"{VID}.en.vtt").read_text()

ts_re = re.compile(r'^(\d{2}):(\d{2}):(\d{2})\.(\d{3}) --> (\d{2}):(\d{2}):(\d{2})\.(\d{3})')
tag_re = re.compile(r'<[^>]+>')

def to_sec(h,m,s,ms): return int(h)*3600+int(m)*60+int(s)+int(ms)/1000

segs = []
cur_start = None
cur_text_lines = []
for line in vtt.splitlines():
    m = ts_re.match(line)
    if m:
        if cur_start is not None and cur_text_lines:
            text = ' '.join(cur_text_lines).strip()
            text = tag_re.sub('', text)
            text = re.sub(r'\s+', ' ', text).strip()
            if text:
                segs.append((cur_start, text))
        cur_start = to_sec(*m.groups()[:4])
        cur_text_lines = []
    elif line.strip() and not line.startswith(('WEBVTT','Kind:','Language:','NOTE')):
        cur_text_lines.append(line)
if cur_start is not None and cur_text_lines:
    text = re.sub(r'\s+', ' ', tag_re.sub('', ' '.join(cur_text_lines))).strip()
    if text: segs.append((cur_start, text))

clean = []
for ts, text in segs:
    if clean and clean[-1][1] == text: continue
    if clean and text.startswith(clean[-1][1]):
        clean[-1] = (clean[-1][0], text); continue
    clean.append((ts, text))

chunks, chunk_text = [], []
chunk_start = clean[0][0] if clean else 0
for ts, text in clean:
    if ts - chunk_start > 30 and chunk_text:
        chunks.append({'t': chunk_start, 'text': ' '.join(chunk_text)})
        chunk_text = []; chunk_start = ts
    chunk_text.append(text)
if chunk_text:
    chunks.append({'t': chunk_start, 'text': ' '.join(chunk_text)})

(HERE / "transcript.json").write_text(json.dumps(chunks, indent=2))
def fmt(t):
    t = int(t); h = t//3600; m = (t%3600)//60; s = t%60
    return f'[{h:02d}:{m:02d}:{s:02d}]'
(HERE / "transcript.txt").write_text('\n\n'.join(f"{fmt(c['t'])} {c['text']}" for c in chunks))
print(f"chunks: {len(chunks)}, chars: {sum(len(c['text']) for c in chunks)}")
