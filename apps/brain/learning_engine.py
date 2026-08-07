"""Makima v7.2 — Learning Engine

Merged replacement for v6 habit_tracker + ContinuousLearner.
Feedback DB: stores thumbs up/down per response with turn_id.
Pattern analyzer: detects time-of-day habits, frequent app sequences, repeated questions.
Proactive suggestion engine: surfaces one suggestion per idle window.

v7.2 upgrades:
- Async DB access via run_in_executor
- Zero-crash resilience with try/except wrappers
- Structured %s-format logging
- Thread-safe asyncio.Lock for shared state
- Input validation on all public APIs
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("makima.learning_engine")


PERSONA_EXTRACTION_PROMPT = """You are Makima's implicit trait extractor. Analyze the user's recent conversation messages
and extract structured personal traits. Respond ONLY with valid JSON matching this schema:
{{
    "music_tastes": {{
        "favorite_artists": ["artist1", "artist2"],
        "genres": ["genre1", "genre2"],
        "moods": ["chill", "energetic", "melancholic"],
        "playlists_referenced": ["playlist_name_1"]
    }},
    "daily_routines": {{
        "work_hours": "{{start}}-{end}",
        "morning_habits": ["habit1", "habit2"],
        "evening_habits": ["habit1"],
        "gym_exercise": true | false,
        "gym_schedule": "e.g., 'monday wednesday friday morning'",
        "sleep_time": "e.g., 'around 11pm'"
    }},
    "tech_preferences": {{
        "preferred_os": "linux/windows/mac/macos/linux_distro",
        "programming_languages": ["python", "rust"],
        "editor": "vscode/neovim/vim/sublime",
        "browser": "chrome/firefox/safari/edge",
        "music_service": "spotify/youtube_music/apple_music/jio_saavn"
    }},
    "style_and_language": {{
        "communication_style": "formal/casual/hinglish/friendly/professional/brief/detailed",
        "response_length_preference": "short/medium/long",
        "tone": "casual/witty/helpful/direct"
    }}
}}

Rules:
1. Only extract information explicitly stated OR strongly implied in the messages.
2. Do NOT fabricate data. If no evidence exists for a field, return an empty list/null.
3. Infer genres/moods from song references, artist mentions, or described listening situations.
4. Detect work hours/gym/routines from casual chat like "just got back from gym at 6pm".
5. Detect tech stack preferences from questions about specific tools, languages, editors.
6. Detect communication style from how the user writes (informal, uses slang, formal, etc.).

Recent Messages:
{messages}"""


class ImplicitTraitExtractor:
    """Extracts user traits (music taste, routines, tech prefs) from conversation history.
    
    Uses an LLM-based extraction prompt to parse implicit signals from natural chat.
    Results are cached per-turn_batch to avoid redundant LLM calls.
    """

    BATCH_SIZE = 8       # Number of turns before re-extraction
    MIN_HISTORY_TURNS = 3  # Minimum history length required before first extraction attempt

    def __init__(self, ai_handler: Any, max_cached_turns: int = 64) -> None:
        self.ai_handler = ai_handler
        self.max_cached_turns = max_cached_turns
        self._turn_buffer: list[dict] = []
        self._last_extraction_turn: int = -1
        self._cached_traits: dict[str, Any] = {}
        self._extraction_in_progress: bool = False
        self._trait_extraction_lock = asyncio.Lock()

    def add_turn(self, role: str, content: str) -> None:
        """Add a conversation turn to the buffer. Trigger extraction when batch threshold met."""
        self._turn_buffer.append({"role": role, "content": content})
        # Keep bounded
        if len(self._turn_buffer) > self.max_cached_turns:
            self._turn_buffer = self._turn_buffer[-self.max_cached_turns:]

    async def maybe_extract(self) -> dict[str, Any]:
        """Check if we should run extraction; if so, do it asynchronously and return cached results."""
        total_turns = len(self._turn_buffer)

        # First extraction only after enough history
        if total_turns < self.MIN_HISTORY_TURNS:
            return {}

        # Check if batch interval has passed
        if total_turns - self._last_extraction_turn < self.BATCH_SIZE:
            return self._cached_traits

        # Prevent concurrent extractions
        async with self._trait_extraction_lock:
            # Double-check inside lock
            if total_turns - self._last_extraction_turn < self.BATCH_SIZE:
                return self._cached_traits

            return await self._run_extraction()

    async def _run_extraction(self) -> dict[str, Any]:
        """Run the LLM-based trait extraction on buffered conversation turns."""
        if self._extraction_in_progress:
            return self._cached_traits
        self._extraction_in_progress = True

        try:
            # Gather user messages from buffer
            user_messages = [
                t["content"] for t in self._turn_buffer
                if t["role"] == "user"
            ]
            if not user_messages:
                return {}

            # Truncate to last ~15 messages to fit context window
            recent = user_messages[-15:]
            msg_text = "\n".join(f"[{i+1}] {m}" for i, m in enumerate(recent))

            prompt = PERSONA_EXTRACTION_PROMPT.format(messages=msg_text)

            try:
                resp = await self.ai_handler.generate(
                    [{"role": "user", "content": prompt}],
                    task="persona_extraction",
                    temperature=0.1,
                    max_tokens=1000,
                )
                text = getattr(resp, "text", "") or ""
                parsed = self.ai_handler.try_parse_json(text)
                if isinstance(parsed, dict):
                    self._cached_traits = parsed
                    self._last_extraction_turn = len(self._turn_buffer)
                    logger.info(
                        "ImplicitTraitExtractor: updated traits (artists=%d, genres=%d, routines=%s)",
                        len(parsed.get("music_tastes", {}).get("favorite_artists", [])),
                        len(parsed.get("music_tastes", {}).get("genres", [])),
                        "yes" if parsed.get("daily_routines") else "no",
                    )
                else:
                    logger.debug("ImplicitTraitExtractor: LLM returned non-dict, skipping")
            except Exception as e:
                logger.warning("ImplicitTraitExtractor: LLM call failed: %s", e)

        finally:
            self._extraction_in_progress = False

        return self._cached_traits

    def get_cached_traits(self) -> dict[str, Any]:
        """Return currently cached traits without triggering extraction."""
        return dict(self._cached_traits) if self._cached_traits else {}


class ProactiveSuppressor:
    """Checks if proactive messages should be suppressed based on context."""

    def __init__(
        self,
        config: dict[str, Any],
        screen_service: Any = None,
        agent_orchestrator: Any = None,
    ) -> None:
        self.config = config.get("proactive", {}) if isinstance(config, dict) else {}
        self.screen = screen_service
        self.orchestrator = agent_orchestrator
        self.idle_min_s: float = float(self.config.get("idle_min_s", 30))
        self.last_interaction: float = time.time()
        self._lock = asyncio.Lock()

    def record_interaction(self) -> None:
        """Record that user interacted with the system."""
        self.last_interaction = time.time()

    async def should_suppress(self, focus_profile: str, tts_playing: bool) -> tuple[bool, str]:
        """Determine if proactive suggestions should be suppressed."""
        try:
            if focus_profile in ("gaming", "meeting"):
                return True, f"{focus_profile}_mode"

            if tts_playing:
                return True, "tts_active"

            # Check active interactive agents
            if self.orchestrator:
                try:
                    status = self.orchestrator.get_status()
                    if isinstance(status, dict):
                        for name, s in status.items():
                            if name == "commander_agent" and isinstance(s, dict):
                                if s.get("state") == "running":
                                    return True, "user_busy"
                except Exception:
                    pass

            # Check idle time
            if time.time() - self.last_interaction < self.idle_min_s:
                return True, "just_interacted"

            # Check fullscreen (via C++ ScreenService)
            if self.screen and self.config.get("fullscreen_check", True):
                try:
                    is_full = await self.screen.check_fullscreen()
                    if is_full:
                        return True, "fullscreen"
                except Exception:
                    pass

            return False, ""
        except Exception as e:
            logger.error("should_suppress failed: %s", e)
            return False, ""


class LearningEngine:
    """Async SQLite-backed learning engine with feedback and pattern tracking."""

    def __init__(
        self,
        db_path: str = "~/.makima/learning.db",
        ws_broadcast: Any = None,
        config: dict[str, Any] | None = None,
        screen_service: Any = None,
        agent_orchestrator: Any = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ws_broadcast = ws_broadcast
        self._config = config or {}
        self.suppressor = ProactiveSuppressor(
            self._config,
            screen_service=screen_service,
            agent_orchestrator=agent_orchestrator,
        )
        self._lock = asyncio.Lock()
        self._initialized = False

    async def start(self) -> None:
        """Initialize the learning engine and create/verify DB tables."""
        try:
            await self._init_db()
            self._initialized = True
            # Lazily initialise trait extractor (requires ai_handler from orchestrator)
            self._trait_extractor: ImplicitTraitExtractor | None = None
            logger.info("LearningEngine started, db=%s", self.db_path)
        except Exception as e:
            logger.error("LearningEngine start failed: %s", e)

    async def stop(self) -> None:
        """Cleanup resources."""
        self._initialized = False
        self._trait_extractor = None
        logger.info("LearningEngine stopped")

    # ── Trait extractor initialisation ──────────────────────────────────────

    def connect_ai_handler(self, ai_handler: Any) -> None:
        """Call this once the AI handler is available (from main.py)."""
        if ai_handler is not None:
            self._trait_extractor = ImplicitTraitExtractor(ai_handler)

    @property
    def cached_user_persona(self) -> dict[str, Any]:
        """Return the most recently extracted user persona snapshot."""
        if self._trait_extractor:
            return self._trait_extractor.get_cached_traits()
        return {}

    # ── Public API — persona & traits ───────────────────────────────────────

    async def get_user_persona_summary(self) -> str:
        """Build a human-readable persona summary string for injection into system prompts.

        Returns a concise paragraph Makima can use to personalise responses.
        If no data yet, returns empty string.
        """
        traits = self.cached_user_persona
        if not traits:
            return ""

        parts: list[str] = []

        # Music tastes
        music = traits.get("music_tastes", {}) or {}
        artists = music.get("favorite_artists", [])
        genres = music.get("genres", [])
        moods = music.get("moods", [])
        playlists = music.get("playlists_referenced", [])
        if artists:
            parts.append(f"User likes artists like {', '.join(str(a) for a in artists[:5])}.")
        if genres:
            parts.append(f"Favourite genres: {', '.join(str(g) for g in genres[:3])}.")
        if moods:
            parts.append(f"Enjoys {', '.join(str(m) for m in moods[:3])} vibes.")
        if playlists:
            parts.append(f"Has playlists: {', '.join(str(p) for p in playlists[:2])}.")

        # Daily routines
        routines = traits.get("daily_routines", {}) or {}
        work = routines.get("work_hours", "")
        morning = routines.get("morning_habits", [])
        evening = routines.get("evening_habits", [])
        gym_sched = routines.get("gym_schedule", "")
        sleep = routines.get("sleep_time", "")
        if work:
            parts.append(f"Works around {work}.")
        if morning:
            parts.append(f"Morning routine includes {', '.join(str(h) for h in morning[:3])}.")
        if evening:
            parts.append(f"Evening routine includes {', '.join(str(h) for h in evening[:2])}.")
        if gym_sched:
            parts.append(f"Gym schedule: {gym_sched}.")
        if sleep:
            parts.append(f"Tends to sleep around {sleep}.")

        # Tech preferences
        tech = traits.get("tech_preferences", {}) or {}
        os_pref = tech.get("preferred_os", "")
        langs = tech.get("programming_languages", [])
        editor = tech.get("editor", "")
        browser = tech.get("browser", "")
        music_svc = tech.get("music_service", "")
        if os_pref:
            parts.append(f"Prefers {os_pref}.")
        if langs:
            parts.append(f"Codes with {', '.join(str(l) for l in langs[:4])}.")
        if editor:
            parts.append(f"Uses {editor}.")
        if browser:
            parts.append(f"Browsing with {browser}.")
        if music_svc:
            parts.append(f"Listens via {music_svc}.")

        # Style / language
        style = traits.get("style_and_language", {}) or {}
        comm = style.get("communication_style", "")
        resp_len = style.get("response_length_preference", "")
        tone = style.get("tone", "")
        if comm:
            parts.append(f"Communicates in a {comm} style.")
        if resp_len:
            parts.append(f"Prefers {'short' if resp_len == 'short' else 'detailed'} responses.")
        if tone:
            parts.append(f"Likes a {tone} conversational tone.")

        return " ".join(parts)

    async def extract_and_store_traits(self, role: str, content: str) -> dict[str, Any]:
        """Add a conversation turn and run extraction if thresholds are met.

        Also persists extracted traits to the user_personas DB table so they
        survive restarts.
        """
        if not self._trait_extractor:
            return {}

        self._trait_extractor.add_turn(role, content)
        traits = await self._trait_extractor.maybe_extract()
        if traits:
            await self._persist_traits(traits)
        return traits

    async def _persist_traits(self, traits: dict[str, Any]) -> None:
        """Write the latest traits snapshot to the user_personas table."""
        def _do_write():
            snapshot = json.dumps(traits, default=str)
            music = traits.get("music_tastes") or {}
            routines = traits.get("daily_routines") or {}
            tech = traits.get("tech_preferences") or {}
            style = traits.get("style_and_language") or {}

            artists_str = ",".join(str(a) for a in music.get("favorite_artists", []))
            genres_str = ",".join(str(g) for g in music.get("genres", []))
            has_routine = 1 if any(routines.values()) else 0
            lang_list = tech.get("programming_languages", []) + [tech.get("editor", "")]
            tech_str = ",".join(str(t) for t in lang_list if t)
            comm_style = style.get("communication_style", "")

            ts = time.time()
            rule_id = str(_uuid.uuid4())

            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.execute(
                    """INSERT INTO user_personas
                       (id, snapshot, music_artists, music_genres, has_routine_data,
                        tech_stack, comm_style, confidence, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (rule_id, snapshot, artists_str, genres_str, has_routine,
                     tech_str, comm_style, 0.7, ts, ts),
                )
                conn.commit()

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_write)
        except Exception as e:
            logger.warning("_persist_traits failed: %s", e)

    # ── Memory-persona recall helper (for agents to query) ──────────────────

    async def get_user_music_taste(self) -> dict[str, Any]:
        """Return cached music-taste subset of the persona."""
        return self.cached_user_persona.get("music_tastes", {}) or {}

    async def get_user_daily_routines(self) -> dict[str, Any]:
        """Return cached daily-routine subset of the persona."""
        return self.cached_user_persona.get("daily_routines", {}) or {}

    async def get_user_tech_prefs(self) -> dict[str, Any]:
        """Return cached tech-preference subset of the persona."""
        return self.cached_user_persona.get("tech_preferences", {}) or {}

    async def _init_db(self) -> None:
        """Create DB tables if they don't exist."""
        def _do_init() -> None:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except Exception:
                    pass
                conn.execute('''CREATE TABLE IF NOT EXISTS feedback (
                    turn_id TEXT PRIMARY KEY,
                    timestamp REAL,
                    is_positive BOOLEAN,
                    category TEXT
                )''')
                conn.execute('''CREATE TABLE IF NOT EXISTS patterns (
                    id TEXT PRIMARY KEY,
                    pattern_type TEXT,
                    description TEXT,
                    confidence REAL,
                    last_seen REAL
                )''')
                # Elite behavior rules table — the core of self-learning.
                # Each rule is a short English/Hinglish sentence that gets injected
                # into LLM prompts so the agent actually changes behavior.
                # category: routing | style | tool | semantic | personality
                # source: feedback | correction | failure | regen | explicit
                conn.execute('''CREATE TABLE IF NOT EXISTS behavior_rules (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    rule_text TEXT NOT NULL,
                    keywords TEXT NOT NULL DEFAULT "",
                    confidence REAL NOT NULL DEFAULT 0.7,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT "feedback",
                    created_at REAL NOT NULL,
                    last_used REAL,
                    is_active INTEGER NOT NULL DEFAULT 1
                )''')
                # Style preferences — aggregated from regen signals and explicit feedback
                conn.execute('''CREATE TABLE IF NOT EXISTS style_prefs (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.6,
                    updated_at REAL NOT NULL
                )''')
                # User persona — implicit traits extracted from conversation (music taste, routines, tech prefs)
                conn.execute('''CREATE TABLE IF NOT EXISTS user_personas (
                    id TEXT PRIMARY KEY,
                    snapshot TEXT NOT NULL,          -- full JSON blob of all extracted traits
                    music_artists TEXT,              -- comma-separated artist names
                    music_genres TEXT,               -- comma-separated genre list
                    has_routine_data INTEGER DEFAULT 0,
                    tech_stack TEXT,                 -- e.g., "python,rust,neovim"
                    comm_style TEXT,                 -- "casual", "hinglish", "formal"
                    confidence REAL NOT NULL DEFAULT 0.5,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )''')
                # Time-pattern tracking for predictive habit triggers
                conn.execute('''CREATE TABLE IF NOT EXISTS time_patterns (
                    id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,            -- e.g. "news", "gym_playlist", "coding"
                    hour INTEGER NOT NULL,           -- 0-23
                    day_of_week INTEGER NOT NULL,    -- 0-6 (Mon-Sun)
                    count INTEGER NOT NULL DEFAULT 1,
                    suggestion TEXT NOT NULL,
                    last_seen REAL NOT NULL
                )''')
                conn.execute("CREATE INDEX IF NOT EXISTS idx_time_hour ON time_patterns(hour, day_of_week)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_ts ON feedback(timestamp)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_patterns_conf ON patterns(confidence)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_cat ON behavior_rules(category, is_active)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_conf ON behavior_rules(confidence DESC)")
                conn.commit()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _do_init)

    async def record_feedback(self, turn_id: str, is_positive: bool, category: str = "general") -> str:
        """Record thumbs up/down feedback."""
        if not turn_id or not isinstance(turn_id, str):
            return json.dumps({"error": "Invalid turn_id"})

        category = str(category).strip() or "general"

        def _do_write() -> None:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO feedback (turn_id, timestamp, is_positive, category) VALUES (?, ?, ?, ?)",
                    (turn_id, time.time(), bool(is_positive), category)
                )
                conn.commit()

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_write)
            return json.dumps({"status": "recorded", "turn_id": turn_id, "positive": is_positive})
        except Exception as e:
            logger.error("record_feedback failed: %s", e)
            return json.dumps({"error": str(e)})

    async def get_feedback_stats(self) -> str:
        """Get feedback statistics."""
        def _do_read() -> dict[str, Any]:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                total = conn.execute("SELECT COUNT(*) as cnt FROM feedback").fetchone()
                positive = conn.execute("SELECT COUNT(*) as cnt FROM feedback WHERE is_positive = 1").fetchone()
                return {
                    "total": total["cnt"] if total else 0,
                    "positive": positive["cnt"] if positive else 0,
                }

        try:
            loop = asyncio.get_running_loop()
            stats = await loop.run_in_executor(None, _do_read)
            total = stats.get("total", 0)
            positive = stats.get("positive", 0)
            return json.dumps({
                "total_feedback": total,
                "positive_feedback": positive,
                "positive_rate": round(positive / total, 3) if total > 0 else 0.0,
            })
        except Exception as e:
            logger.error("get_feedback_stats failed: %s", e)
            return json.dumps({"error": str(e)})

    async def store_pattern(self, pattern_id: str, pattern_type: str, description: str, confidence: float) -> None:
        """Store or update a detected pattern."""
        if not pattern_id or not description:
            return

        confidence = max(0.0, min(1.0, float(confidence)))

        def _do_write() -> None:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO patterns (id, pattern_type, description, confidence, last_seen) VALUES (?, ?, ?, ?, ?)",
                    (pattern_id, pattern_type, description, confidence, time.time())
                )
                conn.commit()

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_write)
        except Exception as e:
            logger.error("store_pattern failed: %s", e)

    async def get_patterns_for_briefing(self) -> list[str]:
        """Fetch patterns to include in the daily briefing."""
        def _do_read() -> list[str]:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                cursor = conn.execute(
                    "SELECT description FROM patterns WHERE confidence > 0.8 ORDER BY last_seen DESC LIMIT 3"
                )
                return [row[0] for row in cursor.fetchall()]

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _do_read)
        except Exception as e:
            logger.error("get_patterns_for_briefing failed: %s", e)
            return []

    async def get_all_patterns(self) -> str:
        """Get all patterns as JSON."""
        def _do_read() -> list[dict[str, Any]]:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id, pattern_type, description, confidence, last_seen "
                    "FROM patterns ORDER BY confidence DESC LIMIT 50"
                ).fetchall()
                return [dict(r) for r in rows]

        try:
            loop = asyncio.get_running_loop()
            patterns = await loop.run_in_executor(None, _do_read)
            return json.dumps(patterns, default=str)
        except Exception as e:
            logger.error("get_all_patterns failed: %s", e)
            return json.dumps([])

    async def store_rule(
        self,
        category: str,
        rule_text: str,
        keywords: list = None,
        confidence: float = 0.7,
        source: str = "feedback",
    ) -> str:
        """Store an actionable behavior rule extracted from a learning signal.
        category: routing | style | tool | semantic | personality
        Returns the rule_id on success, empty string on failure.
        """
        if not rule_text or not isinstance(rule_text, str):
            return ""
        category = str(category).strip() or "general"
        source = str(source).strip() or "feedback"
        confidence = max(0.0, min(1.0, float(confidence)))
        kw_str = ",".join(str(k).strip().lower() for k in (keywords or []) if k)
        rule_id = str(_uuid.uuid4())

        def _do_write() -> None:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.execute(
                    """INSERT INTO behavior_rules
                       (id, category, rule_text, keywords, confidence, source, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (rule_id, category, rule_text.strip(), kw_str, confidence, source, time.time()),
                )
                conn.commit()

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_write)
            logger.info("LearningEngine: stored rule [%s] category=%s src=%s", rule_id[:8], category, source)
            return rule_id
        except Exception as e:
            logger.error("store_rule failed: %s", e)
            return ""

    async def search_behavior_rules(
        self,
        query: str,
        category: str = None,
        top_k: int = 5,
    ) -> list:
        """Retrieve active rules matching the query by keyword + confidence score.
        Used by command_router (routing rules) and base_agent (style/tool rules).
        """
        if not query:
            return []
        query_lower = query.lower()
        query_words = {w for w in query_lower.split() if len(w) > 2}
        top_k = max(1, min(int(top_k), 20))

        def _do_search() -> list:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                sql = (
                    "SELECT id, category, rule_text, keywords, confidence, hit_count "
                    "FROM behavior_rules WHERE is_active = 1 "
                    + ("AND category = ? " if category else "")
                    + "ORDER BY confidence DESC, hit_count DESC LIMIT 80"
                )
                params = (category,) if category else ()
                rows = conn.execute(sql, params).fetchall()
                scored = []
                for r in rows:
                    kws = {k.strip() for k in r["keywords"].split(",") if k.strip()}
                    overlap = len(query_words & kws) + sum(1 for kw in kws if kw in query_lower)
                    score = overlap * 0.4 + float(r["confidence"]) * 0.6
                    scored.append((score, dict(r)))
                scored.sort(key=lambda x: x[0], reverse=True)
                return [item for _, item in scored[:top_k]]

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _do_search)
        except Exception as e:
            logger.error("search_behavior_rules failed: %s", e)
            return []

    async def bump_rule_hit(self, rule_id: str) -> None:
        """Increment hit_count for a rule that was applied — boosts future retrieval."""
        if not rule_id:
            return
        def _do_bump() -> None:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.execute(
                    "UPDATE behavior_rules SET hit_count = hit_count + 1, last_used = ? WHERE id = ?",
                    (time.time(), rule_id),
                )
                conn.commit()
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_bump)
        except Exception as e:
            logger.error("bump_rule_hit failed: %s", e)

    async def set_style_pref(self, key: str, value: str, confidence: float = 0.7) -> None:
        """Store or update a user style preference (e.g. response_length=short)."""
        if not key or not value:
            return
        confidence = max(0.0, min(1.0, float(confidence)))
        def _do_write() -> None:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.execute(
                    """INSERT INTO style_prefs (key, value, confidence, updated_at) VALUES (?, ?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                       confidence=MAX(style_prefs.confidence, excluded.confidence),
                       updated_at=excluded.updated_at""",
                    (key.strip(), value.strip(), confidence, time.time()),
                )
                conn.commit()
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_write)
        except Exception as e:
            logger.error("set_style_pref failed: %s", e)

    async def get_style_prefs(self) -> dict:
        """Return all style preferences as flat dict — injected into agent system prompts."""
        def _do_read() -> dict:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT key, value FROM style_prefs WHERE confidence >= 0.5 ORDER BY updated_at DESC"
                ).fetchall()
                return {r["key"]: r["value"] for r in rows}
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _do_read)
        except Exception as e:
            logger.error("get_style_prefs failed: %s", e)
            return {}

    async def deprecate_weak_rules(self, min_confidence: float = 0.3, max_age_days: float = 30.0) -> int:
        """Soft-delete old, low-confidence, never-used rules. Returns count deprecated."""
        cutoff = time.time() - max_age_days * 86400
        def _do_deprecate() -> int:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                cur = conn.execute(
                    "UPDATE behavior_rules SET is_active = 0 "
                    "WHERE confidence < ? AND (last_used IS NULL OR last_used < ?) AND hit_count < 2",
                    (min_confidence, cutoff),
                )
                conn.commit()
                return cur.rowcount
        try:
            loop = asyncio.get_running_loop()
            count = await loop.run_in_executor(None, _do_deprecate)
            if count:
                logger.info("LearningEngine: deprecated %d weak rules", count)
            return count
        except Exception as e:
            logger.error("deprecate_weak_rules failed: %s", e)
            return 0

    async def get_all_rules_summary(self) -> str:
        """Return JSON summary of active rules grouped by category."""
        def _do_read() -> dict:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT category, COUNT(*) as cnt, AVG(confidence) as avg_conf "
                    "FROM behavior_rules WHERE is_active=1 GROUP BY category ORDER BY cnt DESC"
                ).fetchall()
                return {r["category"]: {"count": r["cnt"], "avg_confidence": round(r["avg_conf"], 2)} for r in rows}
        try:
            loop = asyncio.get_running_loop()
            summary = await loop.run_in_executor(None, _do_read)
            return json.dumps(summary)
        except Exception as e:
            logger.error("get_all_rules_summary failed: %s", e)
            return json.dumps({})


    async def suggest_proactive(self, suggestion: str, focus_profile: str, tts_playing: bool) -> bool:
        """Attempt to surface a proactive suggestion, checking suppressor first."""
        if not suggestion or not isinstance(suggestion, str):
            return False

        try:
            suppress, reason = await self.suppressor.should_suppress(focus_profile, tts_playing)
            if suppress:
                logger.debug("Suppressed proactive suggestion: %s", reason)
                return False

            if self.ws_broadcast:
                try:
                    from . import ws_protocol
                    await self.ws_broadcast(
                        ws_protocol.build_ai_chunk("proactive", f"💡 Suggestion: {suggestion}", is_final=True)
                    )
                except Exception as e:
                    logger.debug("proactive broadcast failed: %s", e)

            return True
        except Exception as e:
            logger.error("suggest_proactive failed: %s", e)
            return False

    async def record_habit_action(self, action: str, suggestion_text: str = "") -> None:
        """Record an observed action with current hour/day_of_week timestamp to build predictive habit clusters."""
        if not action: return
        now_dt = datetime.datetime.now()
        hour = now_dt.hour
        day_of_week = now_dt.weekday()
        pat_id = f"{action}_{hour}_{day_of_week}"
        sugg = suggestion_text or f"Would you like to run {action}?"

        def _do_record():
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.execute(
                    """INSERT INTO time_patterns (id, action, hour, day_of_week, count, suggestion, last_seen)
                       VALUES (?, ?, ?, ?, 1, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET count = time_patterns.count + 1, last_seen = excluded.last_seen""",
                    (pat_id, action, hour, day_of_week, sugg, time.time())
                )
                conn.commit()

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_record)
        except Exception as e:
            logger.error("record_habit_action failed: %s", e)

    async def get_predictive_suggestion(self, hour: int | None = None, day_of_week: int | None = None) -> str | None:
        """Query predictive habit patterns for current time slot and return high-confidence proactive suggestion."""
        now_dt = datetime.datetime.now()
        h = hour if hour is not None else now_dt.hour
        dow = day_of_week if day_of_week is not None else now_dt.weekday()

        def _do_query() -> str | None:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """SELECT suggestion, count FROM time_patterns
                       WHERE hour = ? AND day_of_week = ? AND count >= 2
                       ORDER BY count DESC, last_seen DESC LIMIT 1""",
                    (h, dow)
                ).fetchone()
                if row:
                    return str(row["suggestion"])
                return None

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _do_query)
        except Exception as e:
            logger.error("get_predictive_suggestion failed: %s", e)
            return None
