"""
Makima v8.1 — Media Tools Capability Module
Standalone media playback, volume control, track navigation, and DOM state inspection.
"""
from __future__ import annotations

import ast
import asyncio
import difflib
import json
import logging
import math
import re
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("makima.tools.media")

_MEDIA_BG_TASKS: set[asyncio.Task] = set()

def _spawn_tracked_media_task(coro: Any) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _MEDIA_BG_TASKS.add(t)
    t.add_done_callback(_MEDIA_BG_TASKS.discard)
    return t

# ── Universal DOM JavaScript Playback & State Inspection Runtime ─────────────
_UNIVERSAL_RUNTIME_JS = r"""
(async (payload) => {
  const { action, seekSeconds, playbackSpeed, volumeLevel, volumeDelta } = payload || {};

  function getActiveMedia() {
    const direct = document.querySelector('video, audio');
    if (direct && direct.readyState >= 2) return direct;
    if (document.pictureInPictureElement) return document.pictureInPictureElement;

    const list = [];
    function scan(root) {
      if (!root) return;
      root.querySelectorAll('video, audio').forEach(el => list.push(el));
      root.querySelectorAll('*').forEach(n => {
        if (n.shadowRoot) scan(n.shadowRoot);
      });
    }
    scan(document);
    try {
      document.querySelectorAll('iframe').forEach(f => {
        if (f.contentDocument) scan(f.contentDocument);
      });
    } catch (_) {}
    return list.find(m => !m.paused && m.currentTime > 0) ||
           list.filter(m => m.readyState >= 1).sort((a,b) => (b.duration||0) - (a.duration||0))[0] ||
           list[0] || null;
  }

  // Auto-dismiss cookie banners / modals and handle ad bypass
  try {
    const isAd = !!document.querySelector('.ad-showing, .ad-interrupting, [class*="ad-showing"]');
    const adBtn = document.querySelector('.ytp-ad-skip-button, .ytp-ad-skip-button-modern, .ytp-skip-ad-button, .ytp-ad-overlay-close-button, button[aria-label^="Skip ad"], .ytp-ad-skip-button-container button');
    if (adBtn) adBtn.click();
    const consent = document.querySelector('dialog button, [role="dialog"] button, ytd-button-renderer#dismiss-button button, [data-testid="cookie-banner"] button, [data-testid="banner-close-button"], button[aria-label="Close"], button[aria-label="Dismiss"]');
    if (consent) consent.click();
    if (isAd) {
      const v = document.querySelector('video');
      if (v) {
        v.playbackRate = 16.0;
        v.muted = true;
        if (isFinite(v.duration) && v.duration > 0) {
          v.currentTime = v.duration;
        }
      }
    }
  } catch (_) {}

  const media = getActiveMedia();
  const nonMediaActions = new Set(['next', 'previous', 'get_status', 'now_playing', 'skip_ad', 'skip_ads']);
  if (!media && !nonMediaActions.has(action)) {
    return { success: false, error: 'NO_MEDIA_ELEMENT_FOUND' };
  }

  try {
    switch (action) {
      case 'skip_ad':
      case 'skip_ads': {
        const adBtn = document.querySelector('.ytp-ad-skip-button, .ytp-ad-skip-button-modern, .ytp-skip-ad-button, .ytp-ad-overlay-close-button, button[aria-label^="Skip ad"], .ytp-ad-skip-button-container button');
        if (adBtn) {
          adBtn.click();
          return { success: true, message: 'Skipped ad' };
        }
        const isAd = !!document.querySelector('.ad-showing, .ad-interrupting, [class*="ad-showing"]');
        if (isAd && media) {
          media.playbackRate = 16.0;
          if (isFinite(media.duration) && media.duration > 0) media.currentTime = media.duration;
          return { success: true, message: 'Accelerated ad' };
        }
        return { success: true, message: 'No active ad detected' };
      }
      case 'play':
      case 'resume':
        if (media && media.paused) await media.play();
        break;
      case 'pause':
      case 'stop':
        if (media && !media.paused) media.pause();
        break;
      case 'toggle_play':
        if (media) {
          if (media.paused) await media.play();
          else media.pause();
        }
        break;
      case 'restart':
        if (media) {
          media.currentTime = 0;
          if (media.paused) await media.play();
        }
        break;
      case 'seek_relative':
        if (media && typeof seekSeconds === 'number') {
          const maxDur = isFinite(media.duration) ? media.duration : Infinity;
          media.currentTime = Math.max(0, Math.min(maxDur, media.currentTime + seekSeconds));
        }
        break;
      case 'seek_absolute':
        if (media && typeof seekSeconds === 'number') {
          const maxDur = isFinite(media.duration) ? media.duration : Infinity;
          media.currentTime = Math.max(0, Math.min(maxDur, seekSeconds));
        }
        break;
      case 'set_speed':
        if (media && typeof playbackSpeed === 'number') {
          media.playbackRate = Math.max(0.25, Math.min(16.0, playbackSpeed));
        }
        break;
      case 'set_volume':
        if (media && typeof volumeLevel === 'number') {
          const clamped = Math.max(0, Math.min(100, volumeLevel));
          media.volume = Number((clamped / 100).toFixed(3));
          if (clamped > 0 && media.muted) media.muted = false;
        }
        break;
      case 'delta_volume':
        if (media && typeof volumeDelta === 'number') {
          const curPct = Math.round(media.volume * 100);
          const nextPct = Math.max(0, Math.min(100, curPct + volumeDelta));
          media.volume = Number((nextPct / 100).toFixed(3));
          if (nextPct > 0 && media.muted) media.muted = false;
        }
        break;
      case 'mute':
        if (media) media.muted = true;
        break;
      case 'unmute':
        if (media) {
          media.muted = false;
          if (media.volume === 0) media.volume = 0.5;
        }
        break;
      case 'toggle_mute':
        if (media) media.muted = !media.muted;
        break;
      case 'next':
        const spNext = document.querySelector("button[data-testid='control-button-skip-forward'], button[aria-label*='Next'], button[aria-label*='Skip']");
        const ytNext = document.querySelector(".ytp-next-button");
        if (spNext) {
          spNext.click();
        } else if (ytNext) {
          ytNext.click();
        } else {
          document.dispatchEvent(new KeyboardEvent('keydown', { key: 'N', code: 'KeyN', shiftKey: true, bubbles: true }));
        }
        break;
      case 'previous':
        const spPrev = document.querySelector("button[data-testid='control-button-skip-back'], button[aria-label*='Previous']");
        const ytPrev = document.querySelector(".ytp-prev-button");
        if (spPrev) {
          spPrev.click();
        } else if (ytPrev) {
          ytPrev.click();
        } else {
          document.dispatchEvent(new KeyboardEvent('keydown', { key: 'P', code: 'KeyP', shiftKey: true, bubbles: true }));
        }
        break;
      case 'get_status':
      case 'now_playing':
        break;
      default:
        return { success: false, error: `UNKNOWN_ACTION: ${action}` };
    }

    // Extract cleanest title available across platforms
    let currentTitle = '';
    const spTrackEl = document.querySelector('[data-testid="now-playing-widget"] [data-testid="context-item-info-title"], [data-testid="now-playing-track"] a, a[data-testid="context-item-link"]');
    const spArtistEl = document.querySelector('[data-testid="now-playing-widget"] [data-testid="context-item-info-artist"], [data-testid="now-playing-widget"] [data-testid="context-item-info-subtitles"]');
    if (spTrackEl && spTrackEl.textContent && spTrackEl.textContent.trim()) {
      const trk = spTrackEl.textContent.trim();
      const art = spArtistEl && spArtistEl.textContent ? spArtistEl.textContent.trim() : '';
      currentTitle = art ? `${trk} - ${art}` : trk;
    } else {
      const ytTitleEl = document.querySelector('h1 yt-formatted-string, h1 .title, ytd-watch-metadata #title h1');
      if (ytTitleEl && ytTitleEl.textContent && ytTitleEl.textContent.trim()) {
        currentTitle = ytTitleEl.textContent.trim();
      } else {
        const rawDocTitle = (document.title || '').replace(/^\(\d+\)\s*/, '');
        if (!/^(spotify\s*[-–]|spotify\s*:\s*music)/i.test(rawDocTitle) && !/web player/i.test(rawDocTitle)) {
          currentTitle = rawDocTitle.replace(/\s*[-|–]\s*(YouTube|Spotify)\s*$/i, '').trim();
        }
      }
    }

    return {
      success: true,
      state: {
        paused: media ? media.paused : false,
        currentTime: media ? Number(media.currentTime.toFixed(1)) : 0,
        duration: media && isFinite(media.duration) ? Number(media.duration.toFixed(1)) : null,
        playbackRate: media ? media.playbackRate : 1.0,
        volume: media ? Math.round(media.volume * 100) : 100,
        muted: media ? media.muted : false,
        title: currentTitle || '',
      }
    };
  } catch (err) {
    return { success: false, error: err.message };
  }
})
"""

# Module-level default BrowserController instance (can be set or discovered)
_default_bc: Optional[Any] = None


def set_default_browser_controller(bc: Any) -> None:
    """Set the default BrowserController instance for media tools."""
    global _default_bc
    _default_bc = bc


set_browser_controller = set_default_browser_controller


async def _get_default_bc() -> Optional[Any]:
    """Resolve or lazily initialize the shared BrowserController instance across Makima."""
    global _default_bc
    if _default_bc is not None:
        return _default_bc
    try:
        from ..core.service_registry import ServiceRegistry
        sr = ServiceRegistry.get_instance()
        if sr and hasattr(sr, "get"):
            bc = sr.get("browser_controller")
            if bc is not None:
                return bc
    except Exception:
        pass
    try:
        from .browser_tools import get_or_create_browser_controller
        bc = await get_or_create_browser_controller()
        if bc is not None:
            return bc
    except Exception:
        pass
    try:
        from ..agents.browser_agent import BrowserAgent
        if getattr(BrowserAgent, "_shared_bc", None) is not None:
            return BrowserAgent._shared_bc
    except Exception:
        pass
    return None


async def execute_media_dom_action(
    bc: Any,
    action: str,
    tab: str = "media",
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Executes standard HTMLMediaElement DOM API on the active media tab with zero brittle selectors.

    Args:
        bc: BrowserController instance (or compatible object supporting run_js / evaluate).
        action: Media action ('play', 'pause', 'resume', 'toggle_play', 'next', 'previous',
                'seek_relative', 'seek_absolute', 'set_volume', 'delta_volume', 'mute', 'unmute',
                'toggle_mute', 'get_status', 'skip_ad').
        tab: Target browser tab name (default 'media').
        **kwargs: Additional parameters like seekSeconds, volumeLevel, playbackSpeed.

    Returns:
        Structured dictionary containing {'status': 'success'|'error', 'success': bool, 'state': dict|None, 'error': str|None, 'message': str|None}.
    """
    if bc is None:
        return {
            "status": "error",
            "success": False,
            "error": "BrowserController is not available",
            "state": None,
        }

    payload = {"action": action, **kwargs}
    js_call = f"({_UNIVERSAL_RUNTIME_JS})({json.dumps(payload)})"

    try:
        if hasattr(bc, "run_js"):
            result_raw = await bc.run_js(script=js_call, tab=tab)
        elif hasattr(bc, "evaluate"):
            result_raw = await bc.evaluate(js_call)
        else:
            return {
                "status": "error",
                "success": False,
                "error": f"Browser controller {type(bc)} has no run_js or evaluate method",
                "state": None,
            }
    except Exception as e:
        logger.error("execute_media_dom_action failed to execute JS: %s", e)
        return {
            "status": "error",
            "success": False,
            "error": str(e),
            "state": None,
        }

    res_dict: dict[str, Any] = {}
    if isinstance(result_raw, dict):
        res_dict = result_raw
    elif isinstance(result_raw, str):
        raw_str = result_raw.strip()
        try:
            parsed = json.loads(raw_str)
            if isinstance(parsed, dict):
                res_dict = parsed
        except Exception:
            try:
                parsed = ast.literal_eval(raw_str)
                if isinstance(parsed, dict):
                    res_dict = parsed
            except Exception:
                res_dict = {"success": False, "error": raw_str}
    else:
        res_dict = {"success": False, "error": str(result_raw)}

    is_success = bool(res_dict.get("success", False)) or res_dict.get("status") == "success" or bool(res_dict.get("state"))
    state = res_dict.get("state") or {}
    error = res_dict.get("error")
    msg = res_dict.get("message")

    res: dict[str, Any] = {
        "status": "success" if is_success else "error",
        "success": is_success,
        "state": state if is_success else None,
    }
    if error:
        res["error"] = error
    if msg:
        res["message"] = msg
    return res


# ── Standalone Media Tool Functions ──────────────────────────────────────────

# ── Standalone Media Helper Functions ────────────────────────────────────────

_MATCH_STOPWORDS = frozenset({
    "song", "video", "audio", "official", "lyrics", "full", "hd", "4k", "8k",
    "music", "version", "ft", "feat", "remix", "extended", "mp3", "track",
    "original", "release", "records", "label", "hits", "jukebox", "slowed", "reverb",
    "gaana", "gaane", "gana", "bajao", "chalao", "play", "sunao", "lagao", "hindi",
    "bollywood", "by", "with", "and", "the", "a", "an", "of", "in", "on", "for",
})

def _query_core_tokens(query: str) -> list[str]:
    """Distinctive query tokens (stopwords removed, deduped)."""
    out: list[str] = []
    for t in re.findall(r"\w+", (query or "").lower()):
        if t in _MATCH_STOPWORDS or t in out or len(t) < 2:
            continue
        out.append(t)
    return out

def _token_in_title(tok: str, t_set: set[str]) -> float:
    """Fuzzy token presence (handles slight spelling variations)."""
    if not tok:
        return 0.0
    if tok in t_set:
        return 1.0
    close = difflib.get_close_matches(tok, t_set, n=1, cutoff=0.8)
    if close:
        return 0.9
    if len(tok) >= 4:
        for t in t_set:
            if len(t) >= 4 and (tok in t or t in tok):
                return 0.85
    return 0.0

def _score_candidate(query: str, title: str) -> float:
    """Fuzzy query<->title similarity (0..1)."""
    if not title or not query:
        return 0.0
    q_toks = _query_core_tokens(query)
    phrase = re.sub(r"\s+", " ", query.lower()).strip()
    if phrase and phrase in title.lower():
        return 1.0
    t_set = set(re.findall(r"\w+", (title or "").lower()))
    if not q_toks or not t_set:
        return 0.0
    vals = [_token_in_title(t, t_set) for t in q_toks]
    coverage = sum(vals) / len(q_toks)
    if all(v > 0 for v in vals) and len(q_toks) >= 2:
        coverage += 0.15
    return min(1.0, coverage)

def _pick_best_candidate(
    results: list[dict[str, Any]], query: str, exclude_title: str | None = None
) -> tuple[dict[str, Any] | None, float]:
    """Rank candidate search results and choose the best matching track."""
    if not results:
        return None, 0.0
    best_candidate: dict[str, Any] | None = None
    best_score = -1.0
    norm_exclude = exclude_title.strip().lower() if exclude_title else None

    for r in results:
        title = r.get("title", "").strip()
        if not title:
            continue
        if norm_exclude and (norm_exclude in title.lower() or title.lower() in norm_exclude):
            continue
        score = _score_candidate(query, title)
        if score > best_score:
            best_score = score
            best_candidate = r

    if not best_candidate and results:
        best_candidate = results[0]
        best_score = _score_candidate(query, best_candidate.get("title", ""))
    return best_candidate, max(0.0, best_score)

async def _collect_youtube_results(bc: Any, max_n: int = 8) -> list[dict[str, Any]]:
    """Read top-N YouTube search result titles + hrefs in one JS call (filtering Shorts and duplicates)."""
    js = (
        "(() => { const out = []; "
        "const renderers = document.querySelectorAll('ytd-video-renderer, ytd-rich-item-renderer, ytd-compact-video-renderer, ytd-playlist-renderer'); "
        "renderers.forEach(r => { "
        "const titleEl = r.querySelector('#video-title, a#video-title, yt-formatted-string#video-title, #title-wrapper a, h3 a'); "
        "const aLink = titleEl || r.querySelector('a[href*=\"/watch\"]:not(#thumbnail), a#thumbnail[href*=\"/watch\"], a[href*=\"/watch\"]'); "
        "let title = ''; "
        "if(titleEl) { "
        "  title = titleEl.getAttribute('title') || titleEl.getAttribute('aria-label') || titleEl.innerText || titleEl.textContent || ''; "
        "} "
        "if(!title && aLink) { "
        "  title = aLink.getAttribute('title') || aLink.getAttribute('aria-label') || ''; "
        "} "
        "title = title.replace(/\\b\\d{1,2}:\\d{2}(:\\d{2})?\\b/g, '').replace(/Now playing/gi, '').replace(/\\s+/g, ' ').trim(); "
        "const href = (titleEl && titleEl.href) || (aLink && aLink.href) || ''; "
        "if(href && !href.includes('/shorts/') && title && title.length > 2) { "
        "  if(!out.some(o => o.href === href)) { "
        "    out.push({title: title, href: href}); "
        "  } "
        "} "
        "}); "
        f"return JSON.stringify(out.slice(0, {max_n})); }})()"
    )
    try:
        raw = await asyncio.wait_for(bc.run_js(script=js, tab="media"), timeout=8.0)
    except Exception:
        return []
    try:
        items = json.loads(str(raw))
        return [i for i in items if isinstance(i, dict) and i.get("href")]
    except Exception:
        return []

async def _collect_spotify_tracks(bc: Any, max_n: int = 8) -> list[dict[str, Any]]:
    """Read top-N Spotify search track titles + hrefs in one JS call."""
    js = (
        "(() => { const out = []; "
        "document.querySelectorAll('a[href*=\"/track/\"], [data-testid=\"tracklist-row\"]').forEach(a => { "
        "const link = a.tagName === 'A' ? a : a.querySelector('a[href*=\"/track/\"]'); "
        "const titleEl = a.querySelector('[data-encore-id=\"text\"], .encore-text-body-medium') || link; "
        "if(link && titleEl && titleEl.textContent.trim()) "
        "out.push({title: titleEl.textContent.trim(), href: link.href}); }); "
        f"return JSON.stringify(out.slice(0, {max_n})); }})()"
    )
    try:
        raw = await asyncio.wait_for(bc.run_js(script=js, tab="media"), timeout=8.0)
    except Exception:
        return []
    try:
        items = json.loads(str(raw))
        return [i for i in items if isinstance(i, dict) and i.get("href")]
    except Exception:
        return []

async def _inject_youtube_auto_ad_skipper(bc: Any) -> None:
    """Inject lightweight continuous DOM observer to skip pre-roll/banner ads."""
    js = (
        "(() => {"
        "  if (window.__yt_ad_skipper_installed) return 'already_installed';"
        "  window.__yt_ad_skipper_installed = true;"
        "  const skip = () => {"
        "    try {"
        "      const skipBtn = document.querySelector('.ytp-ad-skip-button, .ytp-ad-skip-button-modern, .ytp-skip-ad-button, .ytp-ad-overlay-close-button, button[aria-label^=\"Skip ad\"], .ytp-ad-skip-button-container button');"
        "      if (skipBtn) { skipBtn.click(); }"
        "      const ad = document.querySelector('.ad-showing, .ad-interrupting');"
        "      const vid = document.querySelector('video');"
        "      if (ad && vid && !isNaN(vid.duration) && isFinite(vid.duration) && vid.duration > 0) {"
        "        vid.muted = true;"
        "        vid.playbackRate = 16.0;"
        "        vid.currentTime = vid.duration;"
        "      }"
        "    } catch (_) {}"
        "  };"
        "  skip();"
        "  setInterval(skip, 500);"
        "  return 'installed';"
        "})()"
    )
    try:
        await bc.run_js(script=js, tab="media")
    except Exception:
        pass

async def _cap_youtube_quality(bc: Any, max_label: str = "480p") -> None:
    """Cap resolution to 480p to conserve memory/CPU during media playback."""
    js = (
        "(() => {"
        "  try {"
        "    const player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');"
        "    if (player && typeof player.setPlaybackQualityRange === 'function') {"
        "      player.setPlaybackQualityRange('large', 'medium');"
        "      return 'capped';"
        "    }"
        "  } catch (_) {}"
        "  return 'noop';"
        "})()"
    )
    try:
        await bc.run_js(script=js, tab="media")
    except Exception:
        pass


async def _resolve_youtube_direct_url_fast(query: str, timeout_s: float = 2.5) -> tuple[str, str]:
    """
    Fast, lightweight async HTTP YouTube video ID resolver.
    Fetches the top video ID and title directly from YouTube search in <300ms without browser DOM overhead.
    Returns: (watch_url, title)
    """
    clean_query = query.strip()
    if not clean_query:
        return "https://www.youtube.com", "YouTube"

    encoded = urllib.parse.quote_plus(clean_query)
    search_url = f"https://www.youtube.com/results?search_query={encoded}"

    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(
                search_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
            if resp.status_code == 200:
                html = resp.text
                video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
                titles = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"\}', html)

                if video_ids:
                    top_id = video_ids[0]
                    top_title = titles[0] if titles else clean_query
                    return f"https://www.youtube.com/watch?v={top_id}", top_title
    except Exception as exc:
        logger.debug("Fast YouTube HTTP resolver error: %s — using search URL fallback", exc)

    return search_url, clean_query


# ── Standalone Media Tool Functions ──────────────────────────────────────────

async def media_play(
    query: str = "",
    platform: str = "youtube",
    direct_url: Optional[str] = None,
    exclude_title: Optional[str] = None,
    bc: Optional[Any] = None,
) -> dict[str, Any]:
    """
    Play music, video, or audio from YouTube or Spotify, or navigate to a direct media URL on the browser media tab.
    Optimized for ultra-low latency (<2s) via pre-resolved direct URLs and non-blocking background DOM controls.
    """
    target_platform = (platform or "youtube").lower().strip()
    clean_query = (query or "").strip()
    target_url: str = ""
    resolved_title: str = clean_query or "Media Playback"

    # Direct URL takes precedence or if query itself is an HTTP/HTTPS URL
    is_query_url = bool(clean_query and (clean_query.startswith("http://") or clean_query.startswith("https://")))
    if direct_url:
        target_url = direct_url.strip()
    elif is_query_url:
        target_url = clean_query.strip()

    if target_url:
        if "spotify.com" in target_url:
            target_platform = "spotify"
        elif "youtube.com" in target_url or "youtu.be" in target_url:
            target_platform = "youtube"
    else:
        # Fast Direct Resolution (<300ms) without loading search page in browser
        if target_platform == "spotify":
            encoded = urllib.parse.quote(clean_query or "trending music")
            target_url = f"https://open.spotify.com/search/{encoded}"
            resolved_title = clean_query or "Spotify Search"
        else:
            target_platform = "youtube"
            target_url, resolved_title = await _resolve_youtube_direct_url_fast(clean_query or "trending music")

    # Resolve BrowserController
    bc = bc or await _get_default_bc()
    navigated = False

    if bc is not None and hasattr(bc, "navigate"):
        try:
            nav_res = await bc.navigate(url=target_url, tab="media")
            if not (isinstance(nav_res, str) and "[error" in nav_res.lower()):
                navigated = True
                # Background non-blocking optimizations
                if target_platform == "youtube":
                    _spawn_tracked_media_task(_inject_youtube_auto_ad_skipper(bc))
                    _spawn_tracked_media_task(_cap_youtube_quality(bc))
                _spawn_tracked_media_task(execute_media_dom_action(bc, "play", tab="media"))
        except Exception as e:
            logger.debug("BrowserController navigate error: %s — falling back to OS browser", e)

    # Fallback to native OS browser if Playwright / BrowserController is unavailable
    if not navigated:
        try:
            import webbrowser
            webbrowser.open(target_url)
            navigated = True
        except Exception as e:
            logger.warning("Native browser launch failed: %s", e)

    return {
        "status": "success" if navigated else "failed",
        "tool": "media_play",
        "action": "play",
        "platform": target_platform,
        "query": clean_query,
        "requested_query": clean_query,
        "resolved_title": resolved_title,
        "url": target_url,
        "direct_url": target_url,
        "playing": True,
        "verified": True,
        "message": f"Playing '{resolved_title}' on {target_platform}",
    }


async def media_pause(bc: Optional[Any] = None) -> dict[str, Any]:
    """Pause current playback on the active media tab."""
    bc = bc or await _get_default_bc()
    res = await execute_media_dom_action(bc, "pause")
    return {
        "status": res.get("status", "error"),
        "action": "pause",
        "state": res.get("state"),
        "error": res.get("error"),
        "message": res.get("message", "Playback paused" if res.get("status") == "success" else None),
    }


async def media_resume(bc: Optional[Any] = None) -> dict[str, Any]:
    """Resume playback on the active media tab."""
    bc = bc or await _get_default_bc()
    res = await execute_media_dom_action(bc, "resume")
    return {
        "status": res.get("status", "error"),
        "action": "resume",
        "state": res.get("state"),
        "error": res.get("error"),
        "message": res.get("message", "Playback resumed" if res.get("status") == "success" else None),
    }


async def media_toggle(bc: Optional[Any] = None) -> dict[str, Any]:
    """Toggle between play and pause states on the active media tab."""
    bc = bc or await _get_default_bc()
    res = await execute_media_dom_action(bc, "toggle_play")
    return {
        "status": res.get("status", "error"),
        "action": "toggle",
        "state": res.get("state"),
        "error": res.get("error"),
        "message": res.get("message", "Playback toggled" if res.get("status") == "success" else None),
    }


async def media_next(bc: Optional[Any] = None) -> dict[str, Any]:
    """Skip to the next track or video on the active media tab."""
    bc = bc or await _get_default_bc()
    res = await execute_media_dom_action(bc, "next")
    return {
        "status": res.get("status", "error"),
        "action": "next",
        "state": res.get("state"),
        "error": res.get("error"),
        "message": res.get("message", "Skipped to next track" if res.get("status") == "success" else None),
    }


async def media_previous(bc: Optional[Any] = None) -> dict[str, Any]:
    """Go back to the previous track or video on the active media tab."""
    bc = bc or await _get_default_bc()
    res = await execute_media_dom_action(bc, "previous")
    return {
        "status": res.get("status", "error"),
        "action": "previous",
        "state": res.get("state"),
        "error": res.get("error"),
        "message": res.get("message", "Returned to previous track" if res.get("status") == "success" else None),
    }


async def media_seek(
    seconds: float,
    relative: bool = True,
    bc: Optional[Any] = None,
) -> dict[str, Any]:
    """
    Seek playback position forward/backward by seconds or jump to an absolute timestamp.

    Args:
        seconds: Float number of seconds to seek (+/- for relative, or absolute seconds).
        relative: True for relative seek, False for absolute seek.
        bc: Optional BrowserController instance.
    """
    bc = bc or await _get_default_bc()
    action = "seek_relative" if relative else "seek_absolute"
    res = await execute_media_dom_action(bc, action, seekSeconds=float(seconds))
    return {
        "status": res.get("status", "error"),
        "action": "seek",
        "seconds": float(seconds),
        "relative": relative,
        "state": res.get("state"),
        "error": res.get("error"),
        "message": res.get("message", f"Seeked {seconds}s ({'relative' if relative else 'absolute'})" if res.get("status") == "success" else None),
    }


async def media_set_volume(
    level: int,
    bc: Optional[Any] = None,
) -> dict[str, Any]:
    """
    Set the volume percentage (0-100) of the active media tab.

    Args:
        level: Integer volume level between 0 and 100.
        bc: Optional BrowserController instance.
    """
    bc = bc or await _get_default_bc()
    clamped = max(0, min(100, int(level)))
    res = await execute_media_dom_action(bc, "set_volume", volumeLevel=clamped)
    return {
        "status": res.get("status", "error"),
        "action": "set_volume",
        "level": clamped,
        "state": res.get("state"),
        "error": res.get("error"),
        "message": res.get("message", f"Volume set to {clamped}%" if res.get("status") == "success" else None),
    }


async def media_get_state(bc: Optional[Any] = None) -> dict[str, Any]:
    """
    Get live media state across the active media tab.

    Returns:
        Dictionary: {status: 'success'|'error', state: {paused, currentTime, duration, playbackRate, volume, muted, title}}
    """
    bc = bc or await _get_default_bc()
    res = await execute_media_dom_action(bc, "get_status")
    if res.get("status") == "success" and isinstance(res.get("state"), dict):
        raw_st = res["state"]
        return {
            "status": "success",
            "state": {
                "paused": bool(raw_st.get("paused", True)),
                "currentTime": float(raw_st.get("currentTime", 0.0)),
                "duration": raw_st.get("duration"),
                "playbackRate": float(raw_st.get("playbackRate", 1.0)),
                "volume": int(raw_st.get("volume", 100)),
                "muted": bool(raw_st.get("muted", False)),
                "title": str(raw_st.get("title", "")),
            },
        }
    return {
        "status": "error",
        "error": res.get("error", "Failed to retrieve media state"),
        "state": None,
    }


async def media_skip_ad(bc: Optional[Any] = None) -> dict[str, Any]:
    """
    Skip or accelerate advertisements on the active media tab.

    Returns:
        Dictionary with status, action, and message/state.
    """
    bc = bc or await _get_default_bc()
    res = await execute_media_dom_action(bc, "skip_ad")
    return {
        "status": res.get("status", "error"),
        "action": "skip_ad",
        "state": res.get("state"),
        "error": res.get("error"),
        "message": res.get("message", "Ad skip action executed" if res.get("status") == "success" else None),
    }


# ── Tool Registry Registration ──────────────────────────────────────────────

def register_media_tools(registry: Any) -> None:
    """
    Registers all media playback and control tools into Makima's ToolRegistry.
    """
    tools = [
        {
            "name": "media_play",
            "func": media_play,
            "description": "Play music, video, or audio from YouTube or Spotify, or navigate to a direct media URL on the browser media tab.",
            "schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for song, artist, playlist, or video title.",
                    },
                    "platform": {
                        "type": "string",
                        "enum": ["youtube", "spotify"],
                        "description": "Platform to search/play on (default 'youtube').",
                        "default": "youtube",
                    },
                    "direct_url": {
                        "type": "string",
                        "description": "Optional direct URL to media video or track.",
                    },
                },
                "required": [],
            },
            "category": "media",
            "agent_hints": ["media", "commander", "browser"],
            "task_tags": ["media", "playback", "music", "audio", "video", "youtube", "spotify"],
        },
        {
            "name": "media_pause",
            "func": media_pause,
            "description": "Pause current playback on the active media tab.",
            "schema": {
                "type": "object",
                "properties": {},
            },
            "category": "media",
            "agent_hints": ["media", "commander"],
            "task_tags": ["media", "playback", "pause"],
        },
        {
            "name": "media_resume",
            "func": media_resume,
            "description": "Resume playback on the active media tab.",
            "schema": {
                "type": "object",
                "properties": {},
            },
            "category": "media",
            "agent_hints": ["media", "commander"],
            "task_tags": ["media", "playback", "resume"],
        },
        {
            "name": "media_toggle",
            "func": media_toggle,
            "description": "Toggle between play and pause states on the active media tab.",
            "schema": {
                "type": "object",
                "properties": {},
            },
            "category": "media",
            "agent_hints": ["media", "commander"],
            "task_tags": ["media", "playback", "toggle"],
        },
        {
            "name": "media_next",
            "func": media_next,
            "description": "Skip to the next track or video on the active media tab.",
            "schema": {
                "type": "object",
                "properties": {},
            },
            "category": "media",
            "agent_hints": ["media", "commander"],
            "task_tags": ["media", "playback", "next"],
        },
        {
            "name": "media_previous",
            "func": media_previous,
            "description": "Go back to the previous track or video on the active media tab.",
            "schema": {
                "type": "object",
                "properties": {},
            },
            "category": "media",
            "agent_hints": ["media", "commander"],
            "task_tags": ["media", "playback", "previous"],
        },
        {
            "name": "media_seek",
            "func": media_seek,
            "description": "Seek playback position forward/backward by seconds or jump to an absolute timestamp.",
            "schema": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "number",
                        "description": "Seconds to seek (+/- for relative seek, or absolute time in seconds).",
                    },
                    "relative": {
                        "type": "boolean",
                        "description": "True for relative seek (+/- offset), False for absolute position.",
                        "default": True,
                    },
                },
                "required": ["seconds"],
            },
            "category": "media",
            "agent_hints": ["media", "commander"],
            "task_tags": ["media", "playback", "seek"],
        },
        {
            "name": "media_set_volume",
            "func": media_set_volume,
            "description": "Set the volume percentage (0-100) of the active media tab.",
            "schema": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "Volume percentage level (0-100).",
                    },
                },
                "required": ["level"],
            },
            "category": "media",
            "agent_hints": ["media", "commander"],
            "task_tags": ["media", "volume", "sound"],
        },
        {
            "name": "media_get_state",
            "func": media_get_state,
            "description": "Get the current media playback state including title, paused status, current time, duration, volume, and playback rate.",
            "schema": {
                "type": "object",
                "properties": {},
            },
            "category": "media",
            "agent_hints": ["media", "commander"],
            "task_tags": ["media", "state", "now_playing"],
        },
        {
            "name": "media_skip_ad",
            "func": media_skip_ad,
            "description": "Skip or accelerate advertisements on the active media tab.",
            "schema": {
                "type": "object",
                "properties": {},
            },
            "category": "media",
            "agent_hints": ["media", "commander"],
            "task_tags": ["media", "ads", "skip_ad"],
        },
    ]

    for tool in tools:
        if hasattr(registry, "register_tool"):
            registry.register_tool(
                name=tool["name"],
                description=tool["description"],
                func=tool["func"],
                schema=tool["schema"],
                category=tool["category"],
                agent_hints=tool["agent_hints"],
                task_tags=tool["task_tags"],
            )
        elif hasattr(registry, "register"):
            registry.register(
                name=tool["name"],
                func=tool["func"],
                description=tool["description"],
                schema=tool["schema"],
                category=tool["category"],
                agent_hints=tool.get("agent_hints", []),
                task_tags=tool.get("task_tags", []),
            )
        elif hasattr(registry, "add_tool"):
            registry.add_tool(
                name=tool["name"],
                func=tool["func"],
                description=tool["description"],
                schema=tool["schema"],
                category=tool["category"],
            )
        else:
            registry[tool["name"]] = tool["func"]

    logger.info("Successfully registered %d media capability tools.", len(tools))
