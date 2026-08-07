"""
Makima v8.0 — Media Agent

Improvements over v7.1:
  - Single LLM call (no double-call waste). Primary extraction is reliable enough.
  - _execute_with_tools() used for multi-step playback (navigate → search → click).
  - Real-time ws_broadcast progress during play (user sees "Searching Spotify…").
  - Better Hinglish / mood / vibe → music query inference.
  - connector state persists across turns (_last_connector).
  - Browser fallback → OS shell open when Playwright fails.
  - volume: Spotify uses 0-1 float range, YouTube uses JS video.volume.
  - now_playing: document.title is the most reliable cross-platform signal.
  - All selectors verified live against current DOM (July 2026).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .base_agent import BaseAgent

logger = logging.getLogger("makima.agents.media")


class MediaAgent(BaseAgent):
    AGENT_NAME = "media"
    DESCRIPTION = "Music and video control — Spotify & YouTube via browser automation"
    CAPABILITIES = [
        "music_playback", "video_playback", "volume_management",
        "spotify_control", "youtube_control", "media_status",
    ]
    AGENT_TOOLS = [
        "browser_navigate", "browser_click", "browser_fill",
        "browser_press_key", "browser_set_range", "browser_get_text",
        "browser_run_js", "browser_wait_for",
    ]
    TAGS = ["media", "music", "youtube", "spotify", "sound", "playback", "entertainment"]

    SYSTEM_PROMPT = """You are Makima's Media Agent. You control Spotify and YouTube through the browser.

CONNECTORS:
- spotify → https://open.spotify.com
- youtube → https://www.youtube.com (default when user says "YouTube", "video", "watch")

ACTIONS you support:
play(query)   pause   resume   next   previous   mute   unmute   volume(0-100)   now_playing

TOOLS AVAILABLE (use them step by step):
- browser_navigate(url, expected_domain)
- browser_click(selector, text)
- browser_fill(selector, value)
- browser_press_key(key, selector?)
- browser_set_range(selector, value)   ← Spotify volume slider (0-1 scale NOT 0-100)
- browser_get_text()
- browser_run_js(script)
- browser_wait_for(selector, state?)

INTENT INFERENCE RULES:
- Mood/vibe/weather/activity → infer matching music genre or song:
    "baarish" → "baarish songs hindi" or "rainy day lofi"
    "gym" → "gym workout beats"
    "raat ko" → "late night chill hindi"
    "sad" → "sad songs hindi" or "emotional playlist"
- Hinglish: bajao/chalao=play, rok=pause, agla/skip=next, pichla=previous, aawaz=volume, band=mute
- Default connector: spotify. Use youtube when user says "video", "watch", or explicitly mentions YouTube.

RESPONSE FORMAT — always JSON:
{
  "connector": "spotify" | "youtube",
  "action": "play" | "pause" | "resume" | "next" | "previous" | "mute" | "unmute" | "volume" | "now_playing" | "chat",
  "query": "clean search query or null",
  "volume": 0-100 or null,
  "reply": "short user-facing message"
}"""

    # Spotify selectors — verified against live DOM (2026-07)
    _SPOTIFY = {
        "search_input": "input[data-testid='search-input']",
        "play_pause": "button[data-testid='control-button-playpause']",
        "next": "button[data-testid='control-button-skip-forward']",
        "previous": "button[data-testid='control-button-skip-back']",
        "mute": "button[data-testid='volume-bar-toggle-mute-button']",
        "volume_range": "[data-testid='volume-bar'] input[type='range']",  # scale 0-1
        "first_track": "a[href*='/track/']",
    }

    # YouTube selectors — verified against live DOM (2026-07)
    _YOUTUBE = {
        "search_input": "input[name='search_query']",
        "play_pause": "button.ytp-play-button",
        "next": "a.ytp-next-button",
        "previous": "a.ytp-prev-button",
        "mute": "button.ytp-volume-icon",
        "first_video": "ytd-video-renderer a#video-title, a#video-title, a[href*='/watch']",
    }

    def __init__(self, ai_handler=None, memory=None, tool_registry=None,
                 ws_broadcast=None, orchestrator=None, guardrails=None, **kwargs):
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast,
                         orchestrator, guardrails, **kwargs)
        self._last_connector: str = "spotify"
        self._play_history: list[dict] = self._load_history()
        self._last_random_source: str = "random"

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def execute(self, task_id: str, message: str,
                      context: dict[str, Any], entities: dict[str, Any]) -> str:
        self._reset_state()
        msg = (message or "").strip()

        # ── Privacy guard ───────────────────────────────────────────────
        if self._privacy_mode():
            return "Media control is disabled in Privacy Mode. Disable it to use Spotify/YouTube."

        # ── Single LLM intent extraction ────────────────────────────────
        intent = await self._extract_intent(msg)
        action    = intent.get("action", "play")
        connector = intent.get("connector") or self._last_connector
        query     = intent.get("query") or msg
        volume    = intent.get("volume")
        reply     = intent.get("reply", "")

        self._last_connector = connector

        # ── Non-media chat ───────────────────────────────────────────────
        if action == "chat":
            return reply or await self._llm_call(
                self._build_messages(msg, context), task="general"
            )

        # ── Dispatch ─────────────────────────────────────────────────────
        await self._stream_progress(task_id, f"{connector}: {action}" + (f" '{query}'" if query else ""))

        if action == "play":
            if not query:
                # 1-Word Autopilot (Level 5 Supreme AI): pull top learned artist/genre from persona
                try:
                    _le_inst = getattr(self, "_learning_engine", None)
                    if _le_inst and hasattr(_le_inst, "cached_user_persona"):
                        _snap = getattr(_le_inst, "cached_user_persona", {}) or {}
                        _music = _snap.get("music_tastes") or {}
                        _artists = _music.get("favorite_artists", [])
                        _genres = _music.get("genres", [])
                        if _artists:
                            query = str(_artists[0])
                        elif _genres:
                            query = f"{_genres[0]} music"
                except Exception:
                    pass
                if not query:
                    query = "trending lofi music"
            return await self._do_play(task_id, connector, query, msg)

        if action in ("pause", "resume"):
            return await self._do_toggle_play(connector, action)

        if action == "next":
            return await self._do_control(connector, "next", "Next")

        if action == "previous":
            return await self._do_control(connector, "previous", "Previous")

        if action in ("mute", "unmute"):
            return await self._do_mute_toggle(connector)

        if action == "now_playing":
            return await self._do_now_playing(connector)

        if action == "volume":
            if volume is None:
                return "Tell me the volume level (0-100)."
            return await self._do_volume(connector, int(volume))

        return reply or f"Action '{action}' not recognised."

    # ------------------------------------------------------------------
    # Intent extraction — single LLM call
    # ------------------------------------------------------------------

    async def _extract_intent(self, message: str) -> dict[str, Any]:
        """
        One LLM call that returns structured intent JSON.
        Falls back to _llm_parse heuristic on any failure.
        """
        system = self.SYSTEM_PROMPT
        try:
            raw = await self._llm_call(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message},
                ],
                task="entity_extraction",
                temperature=0.05,
                max_tokens=200,
            )
            parsed = self.ai_handler.try_parse_json(raw)
            if isinstance(parsed, dict) and "action" in parsed:
                return parsed
        except Exception as e:
            logger.warning("[media] Primary intent LLM failed: %s", e)

        # Heuristic fallback via _llm_parse
        return await self._llm_parse(
            message=message,
            schema={
                "action": "play|pause|resume|next|previous|mute|unmute|volume|now_playing|chat",
                "connector": "spotify or youtube",
                "query": "music/video search query or null",
                "volume": "0-100 integer or null",
            },
            context_hint=(
                "Hinglish: bajao/chalao=play, rok=pause, agla=next, pichla=previous, "
                "aawaz=volume. Mood words (baarish, gym, sad, raat) → infer music genre."
            ),
        )

    # ------------------------------------------------------------------
    # Play
    # ------------------------------------------------------------------

    async def _do_play(self, task_id: str, connector: str, query: str, original_msg: str) -> str:
        # ── 50/50 random-source split ───────────────────────────────────
        # "koi accha song bajao" / "random gaana" → 50% of the time pick
        # from songs Makima already played for the user (listening history),
        # 50% of the time a good random search pick.
        from_history = False
        if self._is_random_request(query):
            query, from_history = self._pick_random_source(query)
            await self._stream_progress(
                task_id,
                "Playing from your listening history…" if from_history
                else "Picking a good random song…",
            )
        else:
            await self._stream_progress(task_id, f"Searching {connector} for '{query}'…")

        if connector == "spotify":
            res = await self._play_spotify(task_id, query)
        else:
            res = await self._play_youtube(task_id, query, original_msg)

        if from_history:
            res = f"{res} (from your listening history)"
        return res

    async def _play_spotify(self, task_id: str, query: str) -> str:
        encoded = urllib.parse.quote(query)
        search_url = f"https://open.spotify.com/search/{encoded}/tracks"

        nav = await self._use_tool("browser_navigate", url=search_url,
                                   expected_domain="open.spotify.com", tab="media")
        if self._tool_failed(nav):
            return self._os_open(search_url, "Spotify")

        await asyncio.sleep(1.2)
        await self._stream_progress(task_id, "Matching track title…")

        # Read top results and pick the best title match so a wrong track
        # doesn't get played.
        tracks = await self._collect_spotify_tracks(8)
        chosen = None
        score = 0.0
        if tracks:
            chosen, score = self._pick_best_result(tracks, query)
        if chosen and chosen.get("href"):
            await self._use_tool(
                "browser_navigate", url=chosen["href"],
                expected_domain="open.spotify.com", tab="media",
            )
            await asyncio.sleep(1.2)
            await self._use_tool(
                "browser_click",
                selector="button[data-testid='control-button-playpause'], button[aria-label*='Play']",
                text="Play", tab="media",
            )
        else:
            await self._stream_progress(task_id, "Clicking first track…")

            # Read the first result's title BEFORE clicking so we can confirm
            # what actually got queued/played.
            pre_title = await self._read_title("spotify", "pre")

            # Click first track
            click = await self._use_tool("browser_click",
                                         selector=self._SPOTIFY["first_track"],
                                         text="", tab="media")
            if not self._tool_failed(click):
                # Try to click play button that appears
                await asyncio.sleep(0.6)
                await self._use_tool("browser_click",
                                     selector="button[aria-label*='Play'], button[data-testid*='play']",
                                     text="Play", tab="media")

            await asyncio.sleep(1.2)
            title = await self._read_title("spotify", "post") or pre_title or query
            self._remember_played("spotify", title)
            return f"▶ Playing '{title}' on Spotify."

        await asyncio.sleep(1.0)
        title = await self._read_title("spotify", "post") or query

        # ── Post-play verification: retry next-best only when it scores
        #    strictly better (all-zero = non-Latin titles → keep first).
        if tracks:
            runner_up, score2 = self._pick_best_result(tracks, query, exclude=chosen.get("href") if chosen else None)
            if runner_up and runner_up.get("href") and score2 > score:
                await self._stream_progress(task_id, f"'{title}' didn't match — trying next best…")
                await self._use_tool(
                    "browser_navigate", url=runner_up["href"],
                    expected_domain="open.spotify.com", tab="media",
                )
                await asyncio.sleep(1.2)
                await self._use_tool(
                    "browser_click",
                    selector="button[data-testid='control-button-playpause'], button[aria-label*='Play']",
                    text="Play", tab="media",
                )
                await asyncio.sleep(1.0)
                title = await self._read_title("spotify", "post") or title

        self._remember_played("spotify", title)
        if title and self._score_match(query, title) >= 0.5:
            return f"▶ Playing '{title}' on Spotify."
        return f"▶ Playing '{title}' on Spotify (best match found for '{query}')."

    async def _play_youtube(self, task_id: str, query: str, original_msg: str) -> str:
        encoded = urllib.parse.quote(query)
        search_url = f"https://www.youtube.com/results?search_query={encoded}"

        try:
            nav = await asyncio.wait_for(
                self._use_tool("browser_navigate", url=search_url,
                               expected_domain="youtube.com", tab="media"),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            nav = "[timeout]"

        if self._tool_failed(nav):
            return self._os_open(search_url, "YouTube", original_msg)

        await asyncio.sleep(1.2)
        await self._stream_progress(task_id, "Matching video title…")

        results = await self._collect_youtube_results(8)
        chosen = None
        score = 0.0
        if results:
            chosen, score = self._pick_best_result(results, query)
        if chosen and chosen.get("href"):
            await self._use_tool(
                "browser_navigate", url=chosen["href"],
                expected_domain="youtube.com", tab="media",
            )
        else:
            await self._stream_progress(task_id, "Opening first video…")
            # Read the first result's title BEFORE clicking so we can confirm
            # which video actually opened.
            pre_title = await self._read_title("youtube", "pre")

            click = await self._use_tool("browser_click",
                                         selector=self._YOUTUBE["first_video"],
                                         text="", tab="media")
            if self._tool_failed(click):
                # JS fallback
                js = (
                    "(() => {"
                    "  const el = document.querySelector('ytd-video-renderer a#video-title, a[href*=\"/watch\"]');"
                    "  if(el && el.href){ window.location.href = el.href; return el.href; }"
                    "  return 'not_found';"
                    "})()"
                )
                await self._use_tool("browser_run_js", script=js, tab="media")

        # Wait for the watch page to load, kick off playback (autoplay is
        # often blocked in automated browsers), then read the real title.
        for _ in range(8):
            await asyncio.sleep(0.6)
            loc = await self._use_tool(
                "browser_run_js", script="location.pathname", tab="media"
            )
            if "/watch" in str(loc):
                break
        await self._use_tool(
            "browser_run_js",
            script=(
                "(() => { const v = document.querySelector('video'); "
                "if(!v) return 'no_video'; v.muted=false; "
                "if(v.paused){ v.play().catch(()=>{}); } return 'ok'; })()"
            ),
            tab="media",
        )
        await self._cap_youtube_quality()
        await asyncio.sleep(1.0)
        title = await self._read_title("youtube", "post") or query

        # ── Post-play verification: if the opened title clearly doesn't
        #    match the query and a STRICTLY better-scoring candidate exists,
        #    retry once (all-zero scores = Hindi/Devanagari page → keep first).
        retried = False
        if results:
            runner_up, score2 = self._pick_best_result(results, query, exclude=chosen.get("href") if chosen else None)
            if runner_up and runner_up.get("href") and score2 > score:
                await self._stream_progress(task_id, f"'{title}' didn't match — trying next best…")
                await self._use_tool(
                    "browser_navigate", url=runner_up["href"],
                    expected_domain="youtube.com", tab="media",
                )
                for _ in range(8):
                    await asyncio.sleep(0.6)
                    loc = await self._use_tool(
                        "browser_run_js", script="location.pathname", tab="media"
                    )
                    if "/watch" in str(loc):
                        break
                await self._use_tool(
                    "browser_run_js",
                    script=(
                        "(() => { const v = document.querySelector('video'); "
                        "if(!v) return 'no_video'; v.muted=false; "
                        "if(v.paused){ v.play().catch(()=>{}); } return 'ok'; })()"
                    ),
                    tab="media",
                )
                await self._cap_youtube_quality()
                await asyncio.sleep(1.0)
                title = await self._read_title("youtube", "post") or title
                retried = True

        self._remember_played("youtube", title)
        if title and self._score_match(query, title) >= 0.5:
            return f"▶ Playing '{title}' on YouTube."
        if retried or (results and score >= 0.5):
            return f"▶ Playing '{title}' on YouTube (matched: '{query}')."
        return f"▶ Playing '{title}' on YouTube (best match found for '{query}')."

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    async def _ensure_open(self, connector: str) -> None:
        if connector == "spotify":
            await self._use_tool("browser_navigate",
                                 url="https://open.spotify.com",
                                 expected_domain="open.spotify.com", tab="media")
        else:
            await self._use_tool("browser_navigate",
                                 url="https://www.youtube.com",
                                 expected_domain="youtube.com", tab="media")

    async def _do_toggle_play(self, connector: str, action: str) -> str:
        sel_key = "play_pause"
        sel = self._SPOTIFY[sel_key] if connector == "spotify" else self._YOUTUBE[sel_key]
        label = "Play" if action == "resume" else "Pause"
        res = await self._use_tool("browser_click", selector=sel, text=label, tab="media")
        if self._tool_failed(res):
            return f"Could not {action} {connector}. Make sure it's open."
        return "▶ Resumed." if action == "resume" else "⏸ Paused."

    async def _do_control(self, connector: str, key: str, label: str) -> str:
        sel = self._SPOTIFY.get(key) if connector == "spotify" else self._YOUTUBE.get(key)
        if not sel:
            return f"'{key}' not supported for {connector}."
        res = await self._use_tool("browser_click", selector=sel, text=label, tab="media")
        if self._tool_failed(res):
            return f"Could not execute '{label}' on {connector}."
        return f"{'⏭' if key=='next' else '⏮'} {label} track."

    async def _do_mute_toggle(self, connector: str) -> str:
        sel = self._SPOTIFY["mute"] if connector == "spotify" else self._YOUTUBE["mute"]
        res = await self._use_tool("browser_click", selector=sel, text="Mute", tab="media")
        return "🔇 Mute toggled." if not self._tool_failed(res) else f"Could not toggle mute on {connector}."

    async def _read_title(self, connector: str, stage: str = "post") -> str:
        """Read the actual playing video/track title from the media tab DOM.

        stage="pre"  → title of the first result BEFORE clicking (search page)
        stage="post" → title of the currently loaded player page
        Returns "" when nothing readable — callers fall back to the query.
        """
        if connector == "youtube":
            if stage == "pre":
                js = (
                    "(() => { const el = document.querySelector("
                    "'ytd-video-renderer a#video-title, a[href*=\"/watch\"]'); "
                    "return el && el.textContent ? el.textContent.trim() : ''; })()"
                )
            else:
                js = (
                    "(() => { const h = document.querySelector('h1 yt-formatted-string, h1 .title'); "
                    "if (h && h.textContent && h.textContent.trim()) return h.textContent.trim(); "
                    "return document.title.replace(/\\s*[-|]\\s*YouTube\\s*$/i, ''); })()"
                )
        else:
            if stage == "pre":
                js = (
                    "(() => { const el = document.querySelector('a[href*=\"/track/\"]'); "
                    "return el && el.textContent ? el.textContent.trim() : ''; })()"
                )
            else:
                js = "document.title"
        try:
            res = await asyncio.wait_for(
                self._use_tool("browser_run_js", script=js, tab="media"), timeout=8.0
            )
        except Exception:
            return ""
        if self._tool_failed(str(res)) or not str(res).strip():
            return ""
        title = re.sub(
            r"\s*[-|]\s*(YouTube|Spotify)\s*$", "", str(res), flags=re.IGNORECASE
        ).strip()
        return title

    async def _cap_youtube_quality(self, max_label: str = "480p") -> str:
        """Drop YouTube playback quality to reduce CPU/GPU load (freeze guard
        on weak PCs — 1080p/4K video in a headed Chrome can hang the machine).
        Fragile DOM automation by nature; failures are ignored silently."""
        js = (
            "(() => { try { "
            "const gear = document.querySelector('.ytp-settings-button'); "
            "if (!gear) return 'no_gear'; gear.click(); "
            "const labels = [...document.querySelectorAll('.ytp-menuitem-label')]; "
            "const q = labels.find(i => i.textContent.trim() === 'Quality'); "
            "if (!q) return 'no_quality'; q.click(); "
            "const opts = [...document.querySelectorAll('.ytp-quality-menu .ytp-menuitem-label')]; "
            f"const t = opts.find(i => /(360p|480p)/.test(i.textContent)); "
            "if (!t) return 'no_opt'; t.click(); return 'quality_set'; "
            "} catch(e) { return 'err:' + e.message; } })()"
        )
        await asyncio.sleep(0.6)
        try:
            res = await asyncio.wait_for(
                self._use_tool("browser_run_js", script=js, tab="media"), timeout=6.0
            )
            return str(res)
        except Exception:
            return "quality_cap_skipped"

    async def _do_now_playing(self, connector: str) -> str:
        title = await self._read_title(connector, "post")
        if not title:
            return f"Can't determine what's playing on {connector}."
        return f"🎵 Now playing on {connector}: {title}"

    # ------------------------------------------------------------------
    # Title-aware playback (match-before-play)
    # ------------------------------------------------------------------

    _MATCH_STOPWORDS = frozenset({
        "play", "plays", "playing", "song", "songs", "video", "videos",
        "music", "watch", "listen", "official", "lyrics", "lyric", "full",
        "hd", "audio", "the", "a", "an", "and", "of", "for", "to", "me",
        "my", "chalao", "bajao", "gaana", "gaane", "karo", "kijiye", "kar",
        "do", "please", "mera", "mujhe", "ka", "ke", "ki", "hai", "ko",
        "se", "me", "mein", "aur", "bhi", "na", "ho", "th", "nd", "rd",
    })

    def _score_match(self, query: str, title: str) -> float:
        """Fuzzy query↔title similarity (0..1). Token overlap on unicode
        word tokens (works for Hindi too) + whole-phrase bonus."""
        import re as _re
        q_tokens = set(_re.findall(r"\w+", query.lower())) - self._MATCH_STOPWORDS
        t_tokens = set(_re.findall(r"\w+", title.lower())) - self._MATCH_STOPWORDS
        if not q_tokens:
            return 0.0
        phrase = _re.sub(r"\s+", " ", query.lower()).strip()
        if phrase and phrase in title.lower():
            return 1.0
        overlap = len(q_tokens & t_tokens) / len(q_tokens)
        return min(1.0, overlap)

    async def _collect_youtube_results(self, max_n: int = 8) -> list[dict]:
        """Read top-N YouTube search result titles + hrefs in one JS call."""
        js = (
            "(() => { const out = []; "
            "document.querySelectorAll('ytd-video-renderer').forEach(r => { "
            "const a = r.querySelector('a#video-title'); "
            "if(a && a.href) out.push({title: a.textContent.trim(), href: a.href}); }); "
            f"return JSON.stringify(out.slice(0, {max_n})); }})()"
        )
        try:
            raw = await asyncio.wait_for(
                self._use_tool("browser_run_js", script=js, tab="media"), timeout=8.0
            )
        except Exception:
            return []
        try:
            items = json.loads(str(raw))
        except Exception:
            return []
        return [i for i in items if isinstance(i, dict) and i.get("href")]

    async def _collect_spotify_tracks(self, max_n: int = 8) -> list[dict]:
        """Read top-N Spotify search track titles + hrefs in one JS call."""
        js = (
            "(() => { const out = []; "
            "document.querySelectorAll('a[href*=\"/track/\"]').forEach(a => { "
            "if(a.textContent && a.textContent.trim()) "
            "out.push({title: a.textContent.trim(), href: a.href}); }); "
            f"return JSON.stringify(out.slice(0, {max_n})); }})()"
        )
        try:
            raw = await asyncio.wait_for(
                self._use_tool("browser_run_js", script=js, tab="media"), timeout=8.0
            )
        except Exception:
            return []
        try:
            items = json.loads(str(raw))
        except Exception:
            return []
        return [i for i in items if isinstance(i, dict) and i.get("href")]

    def _pick_best_result(self, results: list[dict], query: str,
                          exclude: str | None = None) -> tuple[dict | None, float]:
        """Score all results, return the (best candidate, its score).
        Skips any href in `exclude` (used for the next-best retry)."""
        best, best_score = None, -1.0
        for r in results:
            href = r.get("href", "")
            if exclude and href == exclude:
                continue
            s = self._score_match(query, r.get("title", ""))
            if s > best_score:
                best, best_score = r, s
        return best, max(best_score, 0.0)

    # ------------------------------------------------------------------
    # 50/50 random picks: listening history vs good random searches
    # ------------------------------------------------------------------

    _RANDOM_MARKERS = re.compile(
        r"\b(random|surprise|koi accha|koi acha|koi sa|koi bhi|kuch bhi|"
        r"kuch accha|something good|something nice|anything|kuch sunao|"
        r"kuch suna|kuch accha sa)\b",
        re.IGNORECASE,
    )
    _GENERIC_MUSIC_WORDS = frozenset({
        "music", "song", "songs", "video", "videos", "gaana", "gaane",
        "chalao", "chala", "chal", "bajao", "baja", "play", "playing",
        "karao", "karo", "kar", "do", "sunao", "suno", "listen", "some",
    })
    _RANDOM_QUERIES = [
        "arijit singh best songs 2025",
        "bollywood romantic hits playlist",
        "lofi hindi study mix",
        "top trending english songs 2025",
        "old hindi evergreen classics",
        "punjabi party hits 2025",
        "chill acoustic covers playlist",
        "bollywood energetic dance songs",
        "90s 2000s hindi love songs",
        "international chart toppers 2025",
    ]

    def _is_random_request(self, query: str) -> bool:
        q = (query or "").strip()
        if not q:
            return True
        if self._RANDOM_MARKERS.search(q):
            return True
        tokens = set(re.findall(r"\w+", q.lower()))
        return bool(tokens) and not (tokens - self._GENERIC_MUSIC_WORDS)

    def _pick_random_source(self, query: str) -> tuple[str, bool]:
        """50/50: previously listened song vs good random pick."""
        if self._play_history and random.random() < 0.5:
            self._last_random_source = "history"
            item = random.choice(self._play_history)
            return (item.get("title") or query), True
        self._last_random_source = "random"
        return random.choice(self._RANDOM_QUERIES), False

    @staticmethod
    def _history_path() -> Path:
        return Path(__file__).resolve().parents[3] / "data" / "media_history.json"

    def _load_history(self) -> list[dict]:
        try:
            path = self._history_path()
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return [d for d in data if isinstance(d, dict) and d.get("title")][-200:]
        except Exception:
            logger.warning("[media] failed to load play history")
        return []

    def _remember_played(self, connector: str, title: str) -> None:
        title = (title or "").strip()
        if not title:
            return
        self._play_history = [h for h in self._play_history if h.get("title") != title]
        self._play_history.append({"title": title, "connector": connector, "ts": time.time()})
        self._play_history = self._play_history[-200:]
        try:
            path = self._history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._play_history, f, ensure_ascii=False)
        except Exception:
            logger.warning("[media] failed to persist play history")

    async def _do_volume(self, connector: str, level: int) -> str:
        level = max(0, min(100, level))
        if connector == "youtube":
            frac = round(level / 100, 3)
            js = (
                f"(() => {{ const v = document.querySelector('video'); "
                f"if(!v) return 'no_video'; v.muted=false; v.volume={frac}; return 'ok'; }})()"
            )
            res = await self._use_tool("browser_run_js", script=js, tab="media")
            if self._tool_failed(str(res)) or "no_video" in str(res):
                return f"Could not set volume — no video element found."
            return f"🔊 Volume set to {level}% on YouTube."

        # Spotify: range input is 0-1 scale
        frac = round(level / 100, 2)
        res = await self._use_tool("browser_set_range",
                                   selector=self._SPOTIFY["volume_range"],
                                   value=frac, tab="media")
        if self._tool_failed(str(res)):
            return f"Could not set volume on Spotify."
        return f"🔊 Volume set to {level}% on Spotify."

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _privacy_mode(self) -> bool:
        try:
            cfg = (self.orchestrator.config or {}) if self.orchestrator else {}
            return bool(cfg.get("privacy_mode", False))
        except Exception:
            return False

    @staticmethod
    def _os_open(url: str, label: str, original_msg: str = "") -> str:
        """Fallback: open URL in default/requested browser via OS shell."""
        import os, sys, subprocess
        if sys.platform != "win32":
            return f"Browser automation failed — please open {label} manually."
        msg_lower = original_msg.lower()
        browser = "brave" if "brave" in msg_lower else ("msedge" if any(x in msg_lower for x in ["edge", "msedge"]) else "chrome")
        subprocess.Popen(["cmd", "/c", "start", browser, url], shell=True)
        return f"Opened {label} in {browser.capitalize()}."
