"""
Makima OS — Generic User Preference Learning Engine
Location: apps/brain/core/preference_engine.py

Generic, zero-hardcoding preference learning engine that:
1. Records every capability execution outcome (tool name, params, success, timestamp) into EternalMemory.
2. Detects recurring parameter patterns for tools once an occurrence threshold is reached within a sliding window N.
3. Dynamically injects formatted user preference constraints into the LLM context prior to action generation.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import re
import sqlite3
import time
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger("makima.core.preference_engine")

DEFAULT_WINDOW_SIZE: int = 10
DEFAULT_THRESHOLD: int = 3

_WORD_LIKE = re.compile(r"[A-Za-z0-9_]")


def _word_hit(needle: str, haystack_lower: str) -> bool:
    """Whole-word/phrase match — generic guard so 'app' never matches 'happy'.

    Works for single tokens and multi-word phrases; returns False for
    needles without any word characters instead of raising.
    """
    text = (needle or "").strip().lower()
    if len(text) < 3 or not _WORD_LIKE.search(text):
        return False
    try:
        return re.search(r"\b" + re.escape(text) + r"\b", haystack_lower or "") is not None
    except re.error:
        return False


@contextlib.contextmanager
def _iter_db_connection(memory: Any) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection from an EternalMemory-like object.

    Shared by PreferenceEngine and LearningCoordinator so connection
    resolution logic lives in exactly one place.
    """
    if memory is None:
        raise RuntimeError("Requires an EternalMemory instance or SQLite connection")

    if isinstance(memory, sqlite3.Connection):
        yield memory
        return

    mem_conn = getattr(memory, "_conn", None)
    db_path = getattr(memory, "db_path", None)

    if str(db_path) == ":memory:" and mem_conn is not None:
        yield mem_conn
        return

    if db_path is not None:
        path_str = str(db_path)
        is_uri = path_str.startswith("file:") or "?mode=memory" in path_str
        conn = sqlite3.connect(path_str, check_same_thread=False, uri=is_uri)
        try:
            yield conn
        finally:
            conn.close()
        return

    if mem_conn is not None:
        yield mem_conn
        return

    raise RuntimeError("Unable to resolve active SQLite connection from EternalMemory")


class PreferenceEngine:
    """
    Generic user preference learning engine for Makima OS.
    Operates generically across all tools without any hardcoded tool or parameter names.
    """

    DEFAULT_N: int = DEFAULT_WINDOW_SIZE
    DEFAULT_THRESHOLD: int = DEFAULT_THRESHOLD

    def __init__(
        self,
        eternal_memory: Any | None = None,
        tool_registry: Any | None = None,
        window_size: int = DEFAULT_WINDOW_SIZE,
        threshold: int = DEFAULT_THRESHOLD,
        learning_coordinator: Any | None = None,
    ) -> None:
        self.memory = eternal_memory
        self.tool_registry = tool_registry
        self.learning_coordinator = learning_coordinator
        if self.learning_coordinator is None and self.memory is not None:
            try:
                self.learning_coordinator = LearningCoordinator(eternal_memory=self.memory)
            except Exception as _exc:
                logger.debug("LearningCoordinator lazy init in PreferenceEngine suppressed: %s", _exc)
        self.n: int = int(window_size)
        self.threshold: int = int(threshold)
        # O1: parsed-preference cache (60s TTL, busted on every preference write).
        self._pref_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
        self._schema_initialized: bool = False

    def _bust_pref_cache(self) -> None:
        self._pref_cache = None

    @contextlib.contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """
        Safely yield an active SQLite connection compatible with EternalMemory's
        in-memory or file-backed storage models.
        """
        with _iter_db_connection(self.memory) as conn:
            self._ensure_tables(conn)
            yield conn

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        """Ensure the foundational EternalMemory conversation schema exists."""
        if self._schema_initialized:
            return
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT,
                created_at REAL NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                access_count INTEGER DEFAULT 1,
                last_accessed_at REAL,
                importance REAL DEFAULT 0.7,
                embedding BLOB
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_grp ON conversation(conversation_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_role ON conversation(role)")
        conn.commit()
        self._schema_initialized = True

    # ─────────────────────────────────────────────────────────────────────────
    # THING 1 — RECORD
    # ─────────────────────────────────────────────────────────────────────────
    async def record(self, action: Any, result: Any) -> dict[str, Any]:
        """
        Generic record hook called after tool execution.
        Stores tool usage record without filtering by tool name.
        """
        tool_name = (
            getattr(action, "capability_name", None)
            or getattr(action, "tool_name", None)
        )
        if not tool_name and isinstance(action, dict):
            tool_name = action.get("capability_name") or action.get("tool_name") or action.get("tool")
        if not tool_name:
            tool_name = str(action)

        raw_params = getattr(action, "parameters", None)
        if raw_params is None and isinstance(action, dict):
            raw_params = action.get("parameters") or action.get("params")
        params = dict(raw_params or {})

        is_success: bool = True
        if hasattr(result, "is_success"):
            is_success = bool(result.is_success)
        elif hasattr(result, "error"):
            is_success = result.error is None
        elif isinstance(result, dict):
            if "is_success" in result:
                is_success = bool(result["is_success"])
            elif "error" in result:
                is_success = result["error"] is None

        now = time.time()
        record_data: dict[str, Any] = {
            "type": "tool_usage",
            "tool": str(tool_name),
            "params": params,
            "success": is_success,
            "timestamp": now,
        }

        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO conversation (conversation_id, created_at, role, message) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        f"tool_usage:{tool_name}",
                        now,
                        "tool_usage",
                        json.dumps(record_data, default=str),
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.error("[PreferenceEngine] Failed to record tool usage for '%s': %s", tool_name, exc)

        return record_data

    # ─────────────────────────────────────────────────────────────────────────
    # THING 2 — DETECT PATTERN
    # ─────────────────────────────────────────────────────────────────────────
    async def detect_pattern(self, tool_name: str) -> dict[str, Any] | None:
        """
        Query EternalMemory for the last N usages of tool_name.
        If a specific parameter combination occurs >= THRESHOLD times,
        store it as a confirmed user preference.
        """
        if not tool_name:
            return None

        usages: list[dict[str, Any]] = []
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    "SELECT message FROM conversation "
                    "WHERE role = 'tool_usage' AND conversation_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (f"tool_usage:{tool_name}", self.n),
                )
                rows = cursor.fetchall()
                for (msg,) in rows:
                    try:
                        parsed = json.loads(msg)
                        if isinstance(parsed, dict) and parsed.get("type") == "tool_usage":
                            usages.append(parsed)
                    except Exception as parse_err:
                        logger.debug("skip malformed tool_usage row: %s", parse_err)
                        continue
        except Exception as exc:
            logger.error("[PreferenceEngine] Failed querying usages for '%s': %s", tool_name, exc)
            return None

        if not usages:
            return None

        # Filter for successful executions if available
        eligible_usages = [u for u in usages if u.get("success", True)]
        if not eligible_usages:
            eligible_usages = usages

        param_counts: dict[str, int] = {}
        canonical_map: dict[str, dict[str, Any]] = {}

        for usage in eligible_usages:
            p = usage.get("params") or {}
            canonical_key = json.dumps(p, sort_keys=True, default=str)
            param_counts[canonical_key] = param_counts.get(canonical_key, 0) + 1
            canonical_map[canonical_key] = p

        if not param_counts:
            return None

        most_common_key, max_count = max(param_counts.items(), key=lambda item: item[1])
        if max_count < self.threshold:
            return None

        preferred_params = canonical_map[most_common_key]
        total_sample = len(usages)
        confidence = round(max_count / total_sample, 4) if total_sample > 0 else 1.0

        preference_record: dict[str, Any] = {
            "type": "user_preference",
            "tool": str(tool_name),
            "preferred_params": preferred_params,
            "confidence": confidence,
            "active": True,
        }

        now = time.time()
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO conversation (conversation_id, created_at, role, message) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        f"user_preference:{tool_name}",
                        now,
                        "user_preference",
                        json.dumps(preference_record, default=str),
                    ),
                )
                conn.commit()
                self._bust_pref_cache()
        except Exception as exc:
            logger.error("[PreferenceEngine] Failed to store confirmed preference for '%s': %s", tool_name, exc)

        # Synchronize into learned_rules if EternalMemory supports save_rule
        if self.memory and hasattr(self.memory, "save_rule"):
            try:
                param_desc = ", ".join(f"{k}={v}" for k, v in sorted(preferred_params.items()))
                pct = round(confidence * 100)
                rule_text = (
                    f"User preference detected: when using {tool_name}, "
                    f"user typically prefers {param_desc} (confidence: {pct}%)"
                )
                keywords = [str(tool_name).lower()]
                for k, v in preferred_params.items():
                    keywords.append(str(k).lower())
                    keywords.append(str(v).lower())
                await self.memory.save_rule(rule_text, keywords=keywords)
            except Exception as rule_exc:
                logger.debug("[PreferenceEngine] Learned rule sync notice: %s", rule_exc)

        return preference_record

    async def record_and_detect(self, action: Any, result: Any) -> dict[str, Any] | None:
        """
        Combined lifecycle hook invoked post-action execution.
        Records execution outcome and checks if threshold criteria are satisfied.
        """
        record_res = await self.record(action, result)
        tool_name = record_res.get("tool")
        if tool_name:
            return await self.detect_pattern(tool_name)
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # THING 2.5 — FEEDBACK & PENALTY LOOP
    # ─────────────────────────────────────────────────────────────────────────
    async def penalize_preference(
        self,
        tool_name: str,
        penalty: float = 0.35,
        threshold: float = 0.5,
    ) -> dict[str, Any] | None:
        """
        Penalize the confidence score of an active user preference when overridden or rejected.
        If confidence drops below threshold (default 0.5), deactivates the preference and purges
        it from active prompt injection and learned rules.
        """
        if not tool_name:
            return None

        pref = await self.get_preference_for_tool(tool_name)
        if not pref:
            return None

        current_conf = float(pref.get("confidence", 1.0))
        new_conf = round(max(0.0, current_conf - penalty), 4)
        is_active = new_conf >= threshold

        now = time.time()
        updated_pref: dict[str, Any] = {
            "type": "user_preference",
            "tool": str(tool_name),
            "preferred_params": pref.get("preferred_params", {}),
            "confidence": new_conf,
            "active": is_active,
            "penalized_at": now,
        }

        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO conversation (conversation_id, created_at, role, message) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        f"user_preference:{tool_name}",
                        now,
                        "user_preference",
                        json.dumps(updated_pref, default=str),
                    ),
                )
                if not is_active:
                    # Clean up learned_rules for this tool if table exists
                    try:
                        conn.execute(
                            "DELETE FROM learned_rules WHERE rule LIKE ?",
                            (f"%{tool_name}%",),
                        )
                    except Exception as _exc:
                        logger.debug("suppressed: %s", _exc)
                conn.commit()
                self._bust_pref_cache()
        except Exception as exc:
            logger.error("[PreferenceEngine] Failed to persist penalized preference for '%s': %s", tool_name, exc)

        return updated_pref

    async def record_feedback(
        self,
        tool_name: str = "",
        rejected_params: dict[str, Any] | None = None,
        accepted: bool = False,
        actual_params: dict[str, Any] | None = None,
        approved: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Record explicit user feedback or automatic manual override detection for a tool.
        - If accepted=False: penalizes preference confidence, deactivates if < 0.5, and optionally
          records actual_params as new preference training data.
        - If accepted=True: reinforces current confidence.
        """
        if approved is not None:
            accepted = approved

        tool_name = tool_name or ""
        now = time.time()
        result: dict[str, Any] = {
            "status": "recorded",
            "tool": tool_name,
            "tool_name": tool_name,
            "accepted": accepted,
            "rejected_params": rejected_params or {},
            "actual_params": actual_params,
            "timestamp": now,
        }

        if not tool_name:
            return result

        if not accepted:
            penalized = await self.penalize_preference(tool_name, penalty=0.35)
            result["preference"] = penalized
            # If actual_params provided, record it as genuine positive usage
            if actual_params:
                dummy_action = {"capability_name": tool_name, "parameters": actual_params}
                dummy_result = {"is_success": True}
                await self.record(dummy_action, dummy_result)
        else:
            # Positive reinforcement
            pref = await self.get_preference_for_tool(tool_name)
            if pref:
                current_conf = float(pref.get("confidence", 1.0))
                new_conf = round(min(1.0, current_conf + 0.1), 4)
                pref["confidence"] = new_conf
                pref["active"] = True
                pref["reinforced_at"] = now
                try:
                    with self._connection() as conn:
                        conn.execute(
                            "INSERT INTO conversation (conversation_id, created_at, role, message) "
                            "VALUES (?, ?, ?, ?)",
                            (
                                f"user_preference:{tool_name}",
                                now,
                                "user_preference",
                                json.dumps(pref, default=str),
                            ),
                        )
                        conn.commit()
                        self._bust_pref_cache()
                except Exception as exc:
                    logger.error("[PreferenceEngine] Failed to reinforce preference: %s", exc)
                result["preference"] = pref

        return result

    def _resolve_tool_name(self, name: str | None, task_id: str | None = None) -> str | None:
        """Resolve an agent/entity name or task_id to a registered tool name.

        1. Looks up tools executed under task_id via LearningCoordinator.
        2. Direct tool_registry lookup for explicit candidate name.
        3. Agent name to canonical tool category mapping fallback.
        """
        # 1. Look up tool executed under this task_id via LearningCoordinator
        if task_id and self.learning_coordinator and hasattr(self.learning_coordinator, "get_tools_for_task"):
            try:
                task_tools = self.learning_coordinator.get_tools_for_task(task_id)
                if task_tools:
                    return task_tools[-1]
            except Exception as task_err:
                logger.debug("task tool lookup error: %s", task_err)

        candidate = (name or "").strip()
        # 2. Direct registry lookup
        if candidate and self.tool_registry:
            try:
                tools = getattr(self.tool_registry, "_tools", None)
                if isinstance(tools, dict):
                    for key in tools:
                        if str(key).lower() == candidate.lower():
                            return str(key)
                get = getattr(self.tool_registry, "get", None)
                if callable(get):
                    try:
                        if get(candidate) is not None:
                            return candidate
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug("suppressed: %s", exc)

        # 3. Agent name to canonical tool mapping fallback
        if candidate:
            cand_lower = candidate.lower().replace("-", "_")
            agent_map = {
                "browser": "browser_navigate",
                "code": "code_interpreter",
                "media": "media_play",
                "system": "system_control",
                "research": "web_search",
                "voice": "speech_synthesis",
                "messaging": "send_message",
            }
            for prefix, default_tool in agent_map.items():
                if prefix in cand_lower:
                    return default_tool

        return None

    async def record_message_feedback(
        self,
        *,
        task_id: str = "",
        message_id: str = "",
        agent_name: str = "",
        positive: bool = True,
        category: str = "general",
        tool_name: str | None = None,
    ) -> dict[str, Any]:
        """Durably record message-level user feedback (thumbs up/down).

        Always persists a `user_feedback` row so feedback is never silently
        dropped. When the feedback resolves to a registered tool (explicit
        tool_name, agent_name, or task_id execution signal), it additionally
        flows through the standard preference reinforce/penalize path.
        """
        resolved = (tool_name or "").strip() or self._resolve_tool_name(agent_name, task_id)
        record_data: dict[str, Any] = {
            "type": "user_feedback",
            "task_id": task_id,
            "message_id": message_id,
            "agent_name": agent_name,
            "tool": resolved or "",
            "positive": bool(positive),
            "category": category,
            "timestamp": time.time(),
        }
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO conversation (conversation_id, created_at, role, message) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        f"feedback:{task_id or 'general'}",
                        record_data["timestamp"],
                        "user_feedback",
                        json.dumps(record_data, default=str),
                    ),
                )
                conn.commit()
                self._bust_pref_cache()
        except Exception as exc:
            logger.error("[PreferenceEngine] Failed to record message feedback: %s", exc)

        result: dict[str, Any] = {"status": "recorded", **record_data}
        if resolved:
            try:
                pref_res = await self.record_feedback(tool_name=resolved, accepted=bool(positive))
                result["preference"] = pref_res.get("preference")
            except Exception as exc:
                logger.debug("[PreferenceEngine] Feedback preference link error: %s", exc)
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # THING 3 — INJECT
    # ─────────────────────────────────────────────────────────────────────────
    def _load_latest_preferences(self) -> dict[str, dict[str, Any]]:
        """Blocking load+parse of latest user_preference rows (cold path only)."""
        latest_preferences: dict[str, dict[str, Any]] = {}
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    "SELECT conversation_id, message FROM conversation "
                    "WHERE role = 'user_preference' "
                    "ORDER BY created_at DESC"
                )
                for conv_id, msg in cursor.fetchall():
                    try:
                        pref = json.loads(msg)
                        if isinstance(pref, dict) and pref.get("type") == "user_preference":
                            t = pref.get("tool")
                            if t and t not in latest_preferences:
                                latest_preferences[t] = pref
                    except Exception as parse_err:
                        logger.debug("skip malformed preference row: %s", parse_err)
                        continue
        except Exception as exc:
            logger.error("[PreferenceEngine] Failed retrieving preferences: %s", exc)
            return {}
        return latest_preferences

    async def get_relevant_preferences(
        self,
        message: str,
        tool_name: str | None = None,
    ) -> list[str]:
        """
        Query EternalMemory for preferences matching tools relevant to the current user message.
        Formats matching preferences into system prompt context hints.
        """
        clean_msg = (message or "").strip().lower()

        # O1: cached parse (60s TTL) — kills a connection + full-table scan per turn.
        # Empty results are cached too; every preference write busts the cache.
        latest_preferences: dict[str, dict[str, Any]] = {}
        cache_hit = False
        try:
            cached = self._pref_cache
            if cached and (time.time() - cached[0]) < 60.0:
                latest_preferences = cached[1]
                cache_hit = True
        except Exception as _exc:
            logger.debug("suppressed: %s", _exc)
        if not cache_hit:
            latest_preferences = self._load_latest_preferences()
            self._pref_cache = (time.time(), latest_preferences)

        coord = getattr(self, "learning_coordinator", None)
        if not latest_preferences:
            if coord and hasattr(coord, "get_causal_mitigations") and tool_name:
                try:
                    return coord.get_causal_mitigations(tool_name)
                except Exception as _exc:
                    logger.debug("suppressed: %s", _exc)
            return []

        relevant_hints: list[str] = []

        for t_name, pref_data in latest_preferences.items():
            if not pref_data.get("active", True):
                continue
            conf = float(pref_data.get("confidence", 1.0))
            if conf < 0.5:
                continue

            if not self._is_tool_relevant(t_name, clean_msg, tool_name, pref_data):
                continue

            params = pref_data.get("preferred_params") or {}
            pct = round(conf * 100)

            if params:
                param_pairs = [f"{k}={v}" for k, v in sorted(params.items())]
                param_str = ", ".join(param_pairs)
            else:
                param_str = "default parameters"

            hint = (
                f"User preference detected: when using {t_name}, "
                f"user typically prefers {param_str} (confidence: {pct}%)"
            )
            relevant_hints.append(hint)

            if coord and hasattr(coord, "get_causal_mitigations"):
                try:
                    c_hints = coord.get_causal_mitigations(t_name)
                    relevant_hints.extend(c_hints)
                except Exception as _exc:
                    logger.debug("suppressed: %s", _exc)

        if tool_name and coord and hasattr(coord, "get_causal_mitigations") and tool_name not in latest_preferences:
            try:
                c_hints = coord.get_causal_mitigations(tool_name)
                relevant_hints.extend(c_hints)
            except Exception as _exc:
                logger.debug("suppressed: %s", _exc)

        return relevant_hints

    def _is_tool_relevant(
        self,
        tool_name: str,
        message_lower: str,
        explicit_tool: str | None = None,
        pref_data: dict[str, Any] | None = None,
    ) -> bool:
        """
        Evaluate if a tool is relevant to the user query without any hardcoding.
        Checks tool name, sub-tokens, registry metadata (tags/descriptions), and param values.
        """
        if explicit_tool:
            return tool_name.lower() == explicit_tool.lower()

        if not message_lower:
            return True

        t_lower = tool_name.lower()
        # 1. Full tool name as a whole word/phrase ("launch_app" won't match "launch_application").
        if _word_hit(t_lower, message_lower):
            return True

        # 2. Tokenized component matching (e.g. "launch_app" -> "launch", "app")
        tokens = [tok for tok in t_lower.replace("-", "_").split("_") if len(tok) >= 3]
        if any(_word_hit(tok, message_lower) for tok in tokens):
            return True

        # 3. Dynamic ToolRegistry inspection
        if self.tool_registry:
            meta = None
            if hasattr(self.tool_registry, "_tools") and isinstance(self.tool_registry._tools, dict):
                meta = self.tool_registry._tools.get(tool_name)
            elif hasattr(self.tool_registry, "get"):
                meta = self.tool_registry.get(tool_name)

            if meta:
                task_tags = getattr(meta, "task_tags", []) or []
                if any(_word_hit(str(tag), message_lower) for tag in task_tags):
                    return True
                category = getattr(meta, "category", "") or ""
                if _word_hit(str(category), message_lower):
                    return True

        # 4. Parameter value relevance
        if pref_data:
            params = pref_data.get("preferred_params") or {}
            for val in params.values():
                if _word_hit(str(val), message_lower):
                    return True

        return False

    # ─────────────────────────────────────────────────────────────────────────
    # Supporting Query APIs
    # ─────────────────────────────────────────────────────────────────────────
    async def get_tool_usages(self, tool_name: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Retrieve recent raw usage records for a specific tool."""
        lim = limit or self.n
        results: list[dict[str, Any]] = []
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    "SELECT message FROM conversation "
                    "WHERE role = 'tool_usage' AND conversation_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (f"tool_usage:{tool_name}", lim),
                )
                for (msg,) in cursor.fetchall():
                    try:
                        results.append(json.loads(msg))
                    except Exception as _exc:
                        logger.debug("suppressed: %s", _exc)
        except Exception as exc:
            logger.error("[PreferenceEngine] get_tool_usages error for '%s': %s", tool_name, exc)
        return results

    async def get_preference_for_tool(self, tool_name: str) -> dict[str, Any] | None:
        """Retrieve the latest confirmed preference for a specific tool."""
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    "SELECT message FROM conversation "
                    "WHERE role = 'user_preference' AND conversation_id = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (f"user_preference:{tool_name}",),
                )
                row = cursor.fetchone()
                if row:
                    return json.loads(row[0])
        except Exception as exc:
            logger.error("[PreferenceEngine] get_preference_for_tool error for '%s': %s", tool_name, exc)
        return None

    async def get_all_preferences(self) -> list[dict[str, Any]]:
        """Retrieve all distinct confirmed preferences across tools."""
        prefs_by_tool: dict[str, dict[str, Any]] = {}
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    "SELECT message FROM conversation "
                    "WHERE role = 'user_preference' "
                    "ORDER BY created_at DESC"
                )
                for (msg,) in cursor.fetchall():
                    try:
                        data = json.loads(msg)
                        t = data.get("tool")
                        if t and t not in prefs_by_tool:
                            prefs_by_tool[t] = data
                    except Exception as _exc:
                        logger.debug("suppressed: %s", _exc)
        except Exception as exc:
            logger.error("[PreferenceEngine] get_all_preferences error: %s", exc)
        return [
            p for p in prefs_by_tool.values()
            if p.get("active", True) and float(p.get("confidence", 1.0)) >= 0.5
        ]


class LearningCoordinator:
    """Generic sink for execution learning signals (tool_success / tool_failure).

    The ExecutionRuntime emits one signal per tool execution; without a
    coordinator those signals are dropped. This implementation persists
    per-tool aggregate stats (successes, failures, last error) so future
    reflexion/mining has durable data. Never raises; contains zero
    tool-specific names or hardcoded thresholds beyond constructor defaults.
    """

    STATS_DDL = """
        CREATE TABLE IF NOT EXISTS tool_learning_stats (
            tool TEXT PRIMARY KEY,
            successes INTEGER NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at REAL NOT NULL
        )
        """

    CAUSAL_DDL = """
        CREATE TABLE IF NOT EXISTS causal_tool_outcomes (
            id TEXT PRIMARY KEY,
            tool_name TEXT NOT NULL,
            param_signature TEXT NOT NULL,
            error_type TEXT NOT NULL,
            root_cause TEXT NOT NULL,
            mitigation TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            success_count INTEGER NOT NULL DEFAULT 0,
            last_seen_at REAL NOT NULL,
            sample_params TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_causal_tool_lookup ON causal_tool_outcomes (tool_name, error_type);
        """

    def __init__(self, eternal_memory: Any | None = None) -> None:
        self.memory = eternal_memory
        self._task_tools: dict[str, list[str]] = {}
        self._tool_timeouts: dict[str, int] = {}
        self._schema_initialized: bool = False

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        if self._schema_initialized:
            return
        conn.execute(self.STATS_DDL)
        conn.executescript(self.CAUSAL_DDL)
        conn.commit()
        self._schema_initialized = True

    @contextlib.contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with _iter_db_connection(self.memory) as conn:
            self._ensure_tables(conn)
            yield conn

    @staticmethod
    def _compute_param_signature(params: dict[str, Any] | None) -> str:
        """Extract a deterministic, privacy-safe structural signature from tool parameters."""
        if not isinstance(params, dict) or not params:
            return "empty"
        sig_parts: list[str] = []
        for k in sorted(params.keys()):
            if k in ("context", "snapshot_id", "task_id", "session_id"):
                continue
            v = params[k]
            if v is None:
                t = "null"
            elif isinstance(v, bool):
                t = "bool"
            elif isinstance(v, (int, float)):
                t = "num"
            elif isinstance(v, str):
                s = v.strip()
                if not s:
                    t = "empty_str"
                elif ("\\" in s or "/" in s) or (len(s) >= 3 and s[1:3] in (":\\", ":/")):
                    import os
                    t = "abs_path" if os.path.isabs(s) else "rel_path"
                else:
                    t = "str"
            elif isinstance(v, list):
                t = f"list[{len(v)}]" if len(v) <= 5 else "list"
            elif isinstance(v, dict):
                t = "dict"
            else:
                t = type(v).__name__
            sig_parts.append(f"{k}:{t}")
        return "|".join(sig_parts) if sig_parts else "empty"

    @staticmethod
    def _classify_error_and_cause(error: str) -> tuple[str, str, str]:
        """Classify an error message into (error_type, root_cause, mitigation)."""
        err_str = str(error or "").strip()
        err_lower = err_str.lower()

        if any(w in err_lower for w in ("timeout", "timed out")):
            return (
                "TIMEOUT",
                "Execution exceeded wall-clock timeout limit",
                "Scale timeout threshold, reduce payload size, or break into smaller chunks",
            )
        if any(w in err_lower for w in ("parameter mismatch", "typeerror", "unexpected keyword", "missing required", "validation error")):
            return (
                "PARAM_MISMATCH",
                "Parameter types or signature schema incompatible with tool definition",
                "Verify required tool arguments and cast types to match specification",
            )
        if any(w in err_lower for w in ("winerror 3", "filenotfound", "no such file", "cannot find the path")):
            return (
                "PATH_NOT_FOUND",
                "Target file or destination folder does not exist on filesystem",
                "Use absolute path and ensure parent directories exist before invoking tool",
            )
        if any(w in err_lower for w in ("winerror 5", "permission denied", "permissionerror", "access is denied")):
            return (
                "PERMISSION_DENIED",
                "Insufficient filesystem or OS privileges or file locked by another process",
                "Verify file ownership, release conflicting process locks, or verify access rights",
            )
        if any(w in err_lower for w in ("verification failed", "physical verification", "invariant verification")):
            return (
                "VERIFICATION_FAILED",
                "System post-condition assertion failed to confirm physical state change",
                "Inspect system state prerequisites and verify side-effect preconditions",
            )
        if any(w in err_lower for w in ("connection refused", "connecterror", "httperror", "status 404", "status 500", "network")):
            return (
                "CONNECTION_ERROR",
                "Remote network host unreachable or returned HTTP error response",
                "Check remote service health and retry with exponential backoff",
            )
        clean_cause = err_str.split("\n")[0][:100] if err_str else "Unknown execution error"
        return (
            "GENERIC_ERROR",
            clean_cause,
            "Inspect error diagnostics, validate input parameters, and check preconditions",
        )

    async def enqueue_signal(self, signal: dict[str, Any] | None) -> dict[str, Any]:
        """Record one learning signal. Never raises — returns a status dict."""
        try:
            if not isinstance(signal, dict):
                return {"status": "ignored", "reason": "not-a-dict"}
            tool = str(signal.get("tool_name") or signal.get("capability") or "").strip()
            if not tool:
                return {"status": "ignored", "reason": "no-tool"}

            task_id = str(signal.get("task_id") or "").strip()
            if task_id:
                if len(self._task_tools) > 500:
                    self._task_tools.pop(next(iter(self._task_tools)), None)
                self._task_tools.setdefault(task_id, []).append(tool)

            is_success = signal.get("is_success")
            if is_success is None:
                is_success = signal.get("signal_type") != "tool_failure"
            is_success = bool(is_success)
            err_text = str(signal.get("error_text") or signal.get("error") or "")[:300]
            now = time.time()

            raw_params = signal.get("params") or {}
            param_sig = self._compute_param_signature(raw_params if isinstance(raw_params, dict) else {})

            if not is_success and any(t_word in err_text.lower() for t_word in ("timeout", "timed out")):
                self._tool_timeouts[tool] = self._tool_timeouts.get(tool, 0) + 1

            occ_count = 0
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT successes, failures FROM tool_learning_stats WHERE tool = ?",
                    (tool,),
                ).fetchone()
                successes, failures = (int(row[0]), int(row[1])) if row else (0, 0)
                if is_success:
                    successes += 1
                else:
                    failures += 1
                conn.execute(
                    "INSERT INTO tool_learning_stats (tool, successes, failures, last_error, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(tool) DO UPDATE SET successes=excluded.successes, "
                    "failures=excluded.failures, last_error=excluded.last_error, "
                    "updated_at=excluded.updated_at",
                    (tool, successes, failures, err_text if not is_success else "", now),
                )

                if not is_success:
                    err_type, root_cause, mitigation = self._classify_error_and_cause(err_text)
                    outcome_id = f"{tool}:{param_sig}:{err_type}"

                    safe_params: dict[str, Any] = {}
                    if isinstance(raw_params, dict):
                        for pk, pv in raw_params.items():
                            if any(s in str(pk).lower() for s in ("key", "token", "secret", "password", "auth")):
                                safe_params[str(pk)] = "[REDACTED]"
                            elif isinstance(pv, str):
                                safe_params[str(pk)] = pv[:100]
                            elif isinstance(pv, (int, float, bool)):
                                safe_params[str(pk)] = pv
                            else:
                                safe_params[str(pk)] = str(pv)[:50]
                    sample_json = json.dumps(safe_params)

                    conn.execute(
                        """
                        INSERT INTO causal_tool_outcomes (
                            id, tool_name, param_signature, error_type, root_cause, mitigation,
                            confidence, occurrence_count, success_count, last_seen_at, sample_params
                        ) VALUES (?, ?, ?, ?, ?, ?, 1.0, 1, 0, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            occurrence_count = causal_tool_outcomes.occurrence_count + 1,
                            confidence = MIN(1.0, 0.5 + (causal_tool_outcomes.occurrence_count + 1) * 0.1),
                            last_seen_at = excluded.last_seen_at,
                            root_cause = excluded.root_cause,
                            mitigation = excluded.mitigation,
                            sample_params = excluded.sample_params
                        """,
                        (outcome_id, tool, param_sig, err_type, root_cause, mitigation, now, sample_json),
                    )
                    conn.commit()

                    row_c = conn.execute(
                        "SELECT occurrence_count FROM causal_tool_outcomes WHERE id = ?",
                        (outcome_id,),
                    ).fetchone()
                    occ_count = int(row_c[0]) if row_c else 1
                else:
                    try:
                        conn.execute(
                            """
                            UPDATE causal_tool_outcomes
                            SET success_count = success_count + 1,
                                confidence = MAX(0.2, confidence - 0.1)
                            WHERE tool_name = ? AND param_signature = ?
                            """,
                            (tool, param_sig),
                        )
                    except Exception as succ_err:
                        logger.debug("[LearningCoordinator] success uplift update error: %s", succ_err)
                    conn.commit()

            # Closed-loop failure reflection: synthesize structured rule on repeated causal failure
            if not is_success and occ_count >= 2 and self.memory and hasattr(self.memory, "save_rule"):
                try:
                    rule = (
                        f"When calling tool '{tool}' with pattern '{param_sig}': "
                        f"avoid {err_type} ({root_cause}). Mitigation: {mitigation}."
                    )
                    keywords = [tool, err_type.lower(), "error", "mitigation"]
                    if inspect.iscoroutinefunction(self.memory.save_rule):
                        await self.memory.save_rule(rule, keywords=keywords)
                    else:
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(None, self.memory.save_rule, rule, keywords)
                except Exception as ref_err:
                    logger.debug("[LearningCoordinator] causal rule save error: %s", ref_err)
            elif not is_success and failures >= 2 and occ_count < 2 and self.memory and hasattr(self.memory, "save_rule"):
                try:
                    rule = f"When calling tool '{tool}', avoid failure pattern: {err_text[:120]}. Verify arguments before retry."
                    keywords = [tool, "error", "fail"]
                    if inspect.iscoroutinefunction(self.memory.save_rule):
                        await self.memory.save_rule(rule, keywords=keywords)
                    else:
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(None, self.memory.save_rule, rule, keywords)
                except Exception as ref_err:
                    logger.debug("[LearningCoordinator] reflexion rule save error: %s", ref_err)

            return {"status": "recorded", "tool": tool, "successes": successes, "failures": failures}
        except Exception as exc:
            logger.debug("[LearningCoordinator] signal persist error: %s", exc)
            return {"status": "error", "reason": str(exc)[:200]}

    def get_causal_mitigations(self, tool_name: str) -> list[str]:
        """Retrieve high-confidence proactive causal mitigations for a tool."""
        tool = str(tool_name).strip()
        if not tool:
            return []
        mitigations: list[str] = []
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT param_signature, error_type, root_cause, mitigation, confidence, occurrence_count "
                    "FROM causal_tool_outcomes "
                    "WHERE tool_name = ? AND occurrence_count >= 2 "
                    "ORDER BY occurrence_count DESC LIMIT 3",
                    (tool,),
                ).fetchall()
                for sig, err_t, cause, mit, conf, count in rows:
                    mitigations.append(
                        f"Causal guard for '{tool}' ({sig}): avoid {err_t} ({cause}). Mitigation: {mit}"
                    )
        except Exception as exc:
            logger.debug("[LearningCoordinator] get_causal_mitigations error: %s", exc)
        return mitigations

    def get_tools_for_task(self, task_id: str) -> list[str]:
        """Return all tools executed under the given task_id."""
        return list(self._task_tools.get(str(task_id).strip(), []))

    def get_timeout_scale(self, tool_name: str) -> float:
        """Return recommended timeout multiplier (1.0 - 2.5) based on recent timeout history."""
        timeouts = self._tool_timeouts.get(str(tool_name).strip(), 0)
        if timeouts >= 2:
            return 2.0
        elif timeouts == 1:
            return 1.5
        return 1.0

    def get_tool_stats(self, tool_name: str) -> dict[str, Any]:
        """Latest aggregate stats for one tool; empty stats when unknown."""
        try:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT successes, failures, last_error, updated_at "
                    "FROM tool_learning_stats WHERE tool = ?",
                    (tool_name,),
                ).fetchone()
        except Exception as exc:
            logger.debug("[LearningCoordinator] stats read error: %s", exc)
            return {"tool": tool_name, "successes": 0, "failures": 0}
        if not row:
            return {"tool": tool_name, "successes": 0, "failures": 0}
        return {
            "tool": tool_name,
            "successes": int(row[0]),
            "failures": int(row[1]),
            "last_error": row[2] or "",
            "updated_at": row[3],
        }
