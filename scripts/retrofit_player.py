#!/usr/bin/env python3
"""Retrofit video pages with: back-to-library link, embedded YouTube player, click-to-seek.

Idempotent — re-running on an already-retrofitted page is a no-op.

Usage:
  python3 scripts/retrofit_player.py                 # all videos
  python3 scripts/retrofit_player.py <video_id> ...  # specific videos
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEOS_DIR = ROOT / "videos"

BACK_LINK_CSS = """\
.brand-row { display: flex; align-items: center; gap: 18px; min-width: 0; }
.back-link { display: inline-flex; align-items: center; gap: 6px; font-family: var(--mono); font-size: 11px; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); text-decoration: none; padding: 7px 12px 7px 10px; border: 1px solid var(--line); border-radius: 8px; transition: all .15s; }
.back-link:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface); }
.back-link svg { width: 13px; height: 13px; }
@media (max-width: 640px) { .brand { display: none; } }
.thumb iframe { width: 100%; height: 100%; display: block; border: 0; background: #000; position: relative; z-index: 1; }
/* Neutralize legacy decorative overlays so they don't block iframe clicks */
.thumb::after { pointer-events: none !important; display: none !important; }
.thumb .play { display: none !important; }
.thumb.is-floating { position: fixed; bottom: 20px; right: 20px; width: 320px; max-width: 40vw; z-index: 90; transform: translateY(20px); opacity: 0; pointer-events: none; transition: transform .25s, opacity .25s; box-shadow: 0 20px 60px -10px rgba(0,0,0,.7); }
.thumb.is-floating.shown { transform: translateY(0); opacity: 1; pointer-events: auto; }
.thumb-close { position: absolute; top: 6px; right: 6px; background: rgba(0,0,0,.55); border: 0; color: #fff; width: 22px; height: 22px; border-radius: 50%; cursor: pointer; font-size: 14px; line-height: 22px; padding: 0; display: none; z-index: 3; }
.thumb.is-floating .thumb-close { display: block; }
@media (max-width: 880px) { .thumb.is-floating { width: 220px; bottom: 12px; right: 12px; } }
"""

BACK_LINK_HTML = (
    '<div class="brand-row">'
    '<a href="../../library/index.html" class="back-link" title="Back to library">'
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">'
    '<path d="M19 12H5M12 19l-7-7 7-7"/></svg>'
    '<span>Library</span></a>'
    '<div class="brand"><span class="dot"></span>Video Notes</div>'
    '</div>'
)

def iframe_html(video_id: str) -> str:
    return (
        '<div class="thumb" id="thumbWrap">'
        '<button class="thumb-close" id="thumbClose" title="Dock" aria-label="Dock">×</button>'
        f'<iframe id="ytPlayer" '
        f'src="https://www.youtube.com/embed/{video_id}?enablejsapi=1&rel=0&modestbranding=1&playsinline=1" '
        'title="YouTube video player" '
        'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" '
        'allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>'
        '</div>'
    )

YT_PLAYER_JS = r"""
/* ---------- YouTube IFrame Player API (retrofit) ---------- */
let ytPlayer = null;
let ytReady = false;
(function loadYT() {
  if (window.YT && window.YT.Player) { initYTPlayer(); return; }
  const tag = document.createElement('script');
  tag.src = 'https://www.youtube.com/iframe_api';
  document.head.appendChild(tag);
  window.onYouTubeIframeAPIReady = initYTPlayer;
})();
function initYTPlayer() {
  ytPlayer = new YT.Player('ytPlayer', { events: { onReady: () => { ytReady = true; } } });
}
function seekVideo(seconds, opts) {
  opts = opts || {};
  const autoplay = opts.autoplay !== false;
  const scroll = opts.scroll !== false;
  if (ytReady && ytPlayer && typeof ytPlayer.seekTo === 'function') {
    ytPlayer.seekTo(seconds, true);
    if (autoplay) ytPlayer.playVideo();
    if (scroll) {
      const wrap = document.getElementById('thumbWrap');
      if (wrap && !wrap.classList.contains('is-floating')) {
        wrap.scrollIntoView({behavior: 'smooth', block: 'start'});
      }
    }
    return true;
  }
  return false;
}
document.addEventListener('click', function(e) {
  const a = e.target.closest && e.target.closest('a[href*="youtube.com/watch"], a[href*="youtu.be/"]');
  if (!a) return;
  const url = a.getAttribute('href') || '';
  const m = url.match(/[?&]t=(\d+)s?/);
  if (!m) return;
  const seconds = parseInt(m[1], 10);
  if (seekVideo(seconds)) e.preventDefault();
});
window.addEventListener('load', function() {
  const hero = document.querySelector('.hero');
  const wrap = document.getElementById('thumbWrap');
  if (!hero || !wrap) return;
  const sentinel = document.createElement('div');
  sentinel.style.cssText = 'position:absolute;left:0;bottom:0;width:1px;height:1px;pointer-events:none;';
  hero.style.position = 'relative';
  hero.appendChild(sentinel);
  const obs = new IntersectionObserver(function(entries) {
    entries.forEach(function(en) {
      if (en.isIntersecting) {
        wrap.classList.remove('is-floating', 'shown');
      } else if (ytReady) {
        try {
          if (ytPlayer && ytPlayer.getPlayerState && ytPlayer.getPlayerState() === 1) {
            wrap.classList.add('is-floating', 'shown');
          }
        } catch (_) {}
      }
    });
  }, { rootMargin: '0px', threshold: 0 });
  obs.observe(sentinel);
});
document.addEventListener('click', function(e) {
  if (e.target && e.target.id === 'thumbClose') {
    const w = document.getElementById('thumbWrap');
    if (w) w.classList.remove('is-floating', 'shown');
  }
});
/* ---------- /retrofit ---------- */
"""


def patch_file(path: Path) -> str:
    src = path.read_text()
    original = src

    fully_retrofitted = 'class="back-link"' in src and 'id="ytPlayer"' in src
    # CSS-only re-patch: if the new pointer-events fix isn't present, inject just the CSS.
    if fully_retrofitted and 'pointer-events: none !important' in src:
        return "already retrofitted (latest CSS)"
    if fully_retrofitted:
        # Append updated CSS rules. Replace any old block injection by anchoring at </style>.
        src = src.replace('</style>', BACK_LINK_CSS + '\n</style>', 1)
        path.write_text(src)
        return "patched CSS (overlay click fix)"

    # Extract video_id from DATA.video.id or from src URL pattern.
    m = re.search(r'"id":\s*"([\w-]{11})"', src)
    if not m:
        m = re.search(r'youtube\.com/embed/([\w-]{11})', src)
    if not m:
        m = re.search(r'i\.ytimg\.com/vi/([\w-]{11})', src)
    if not m:
        return "ERROR: video_id not found"
    video_id = m.group(1)

    # 1. Inject back-link CSS — append to existing <style> block before closing </style>.
    if '.brand-row' not in src:
        src = src.replace('</style>', BACK_LINK_CSS + '\n</style>', 1)

    # 2. Wrap <div class="brand"> in topbar with brand-row + back-link.
    brand_pattern = re.compile(r'<div class="brand"><span class="dot"></span>Video Notes</div>')
    if 'class="brand-row"' not in src:
        src = brand_pattern.sub(BACK_LINK_HTML, src, count=1)

    # 3. Replace static thumb img with iframe (preserve any siblings like .play).
    thumb_block = re.compile(
        r'<div class="thumb">\s*'
        r'<img id="thumbImg"[^>]*/?>\s*'
        r'(?:<span class="play">[^<]*</span>\s*)?'
        r'</div>',
        re.MULTILINE,
    )
    src, n = thumb_block.subn(iframe_html(video_id), src, count=1)
    if n == 0:
        return f"ERROR: thumb block not matched in {path}"

    # 4. Remove the JS line that sets thumbImg src (it's now an iframe).
    src = re.sub(r'^\s*\$\(\'#thumbImg\'\)\.(src|alt)\s*=.*?;\s*\n', '', src, flags=re.MULTILINE)

    # 5. Inject YT player JS — insert right after the ytLink const definition.
    ytlink_match = re.search(r'const ytLink = .*?;\n', src)
    if not ytlink_match:
        return "ERROR: ytLink definition not found"
    insert_at = ytlink_match.end()
    src = src[:insert_at] + YT_PLAYER_JS + src[insert_at:]

    if src == original:
        return "no changes"

    path.write_text(src)
    return f"retrofitted (video_id={video_id})"


def main():
    targets = sys.argv[1:]
    if targets:
        paths = [VIDEOS_DIR / vid / "index.html" for vid in targets]
    else:
        paths = []
        for entry in sorted(VIDEOS_DIR.iterdir()):
            if entry.is_symlink() or not entry.is_dir():
                continue
            f = entry / "index.html"
            if f.exists():
                paths.append(f)

    for p in paths:
        if not p.exists():
            print(f"{p.parent.name}: MISSING index.html")
            continue
        result = patch_file(p)
        print(f"{p.parent.name}: {result}")


if __name__ == "__main__":
    main()
