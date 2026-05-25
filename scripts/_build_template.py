#!/usr/bin/env python3
"""One-shot helper: derive the locked video template from videos/HGbA6ze0_3M/index.html.

Run this when the canonical reference page changes. The template is what
scripts/build_video.py uses to render new video pages. Do NOT run it as part of
the regular pipeline.
"""
import re
from pathlib import Path

REF = Path(__file__).resolve().parent.parent / "videos" / "HGbA6ze0_3M" / "index.html"
OUT = Path(__file__).resolve().parent / "templates" / "video_template.html"

html = REF.read_text()

# 1. Replace iframe src with a placeholder set on load.
html = re.sub(
    r'src="https://www\.youtube\.com/embed/[\w-]{11}\?[^"]*"',
    'data-src-pattern="https://www.youtube.com/embed/{VIDEO_ID}?enablejsapi=1&rel=0&modestbranding=1&playsinline=1" src=""',
    html, count=1,
)

# 2. Replace the const DATA = {...}; block with a sentinel the build script substitutes.
data_re = re.compile(r'const DATA = \{.*?\n\};', re.DOTALL)
assert data_re.search(html), "Could not find const DATA block"
html = data_re.sub('const DATA = /*__DATA_JSON__*/null;', html, count=1)

# 3. Replace const CHAPTERS = [...]; with DATA.chapters || [].
ch_re = re.compile(r'const CHAPTERS = \[.*?\];', re.DOTALL)
assert ch_re.search(html), "Could not find const CHAPTERS block"
html = ch_re.sub('const CHAPTERS = (DATA && DATA.chapters) || [];', html, count=1)

# 4. Replace the manual "SpaceX's <em>$2T</em>..." hero innerHTML with DATA-driven.
html = html.replace(
    '$(\'#title\').innerHTML = "SpaceX\'s <em>$2T</em> Case, Nvidia\'s Shock Selloff, America Turns on AI";',
    "$('#title').innerHTML = DATA.video.display_title_html || escapeHTML(DATA.video.title);",
)

# 5. Add escapeHTML helper + iframe src setter + document.title setter, inject after the ytLink line.
boot = """
function escapeHTML(s) { return (s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
// Document title + iframe src (data-driven so the same template serves every video)
document.title = DATA.video.title + ' — ' + (DATA.video.channel || 'Video Notes');
(function setupPlayerSrc(){
  const el = document.getElementById('ytPlayer');
  const tmpl = el && el.getAttribute('data-src-pattern');
  if (el && tmpl && DATA.video.id) el.src = tmpl.replace('{VIDEO_ID}', DATA.video.id);
})();
"""
html = html.replace(
    'const ytLink = (t) => `${DATA.video.url}&t=${Math.floor(t)}s`;',
    'const ytLink = (t) => `${DATA.video.url}&t=${Math.floor(t)}s`;\n' + boot,
    1,
)

# 6. Replace the <title> with a placeholder; JS will set it from DATA on load.
html = re.sub(
    r'<title>[^<]+</title>',
    '<title>Video Notes</title>',
    html, count=1,
)

# 7. Defensive defaults so missing optional arrays don't throw.
defenses = [
    ('DATA.quotes.forEach',       '(DATA.quotes || []).forEach'),
    ('DATA.stats.forEach',        '(DATA.stats || []).forEach'),
    ('DATA.resources.forEach',    '(DATA.resources || []).forEach'),
    ('DATA.action_items.forEach', '(DATA.action_items || []).forEach'),
    ('DATA.insights.forEach',     '(DATA.insights || []).forEach'),
    ('DATA.insights.filter',      '(DATA.insights || []).filter'),
    ('DATA.insights.map',         '(DATA.insights || []).map'),
]
for old, new in defenses:
    html = html.replace(old, new)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(html)
print(f"Wrote {OUT} ({len(html)} bytes)")
