"""
Makima OS — Generic User Preference Learning Engine
Location: apps/brain/core/preference_engine.py

Generic, zero-hardcoding preference learning engine that:
1. Records every capability execution outcome (tool name, params, success, timestamp) into EternalMemory.
2. Detects recurring parameter patterns for tools once an occurrence threshold is reached within a sliding window N.
3. Dynamically injects formatted user preference constraints into the LLM context prior to action generation.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import time
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger("makima.core.preference_engine")

DEFAULT_WINDOW_SIZE: int = 10
DEFAULT_THRESHOLD: int = 3


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
    ) -> None:
        self.memory = eternal_memory
        self.tool_registry = tool_registry
        self.n: int = int(window_size)
        self.threshold: int = int(threshold)
        # O1: parsed-preference cache (60s TTL, busted on every preference write).
        self._pref_cache: tuple[float, dict[str, dict[str, Any]]] | None = None

    def _bust_pref_cache(self) -> None:
        self._pref_cache = None

    @contextlib.contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """
        Safely yield an active SQLite connection compatible with EternalMemory's
        in-memory or file-backed storage models.
        """
        if self.memory is None:
            raise RuntimeError("PreferenceEngine requires an EternalMemory instance or SQLite connection")

        if isinstance(self.memory, sqlite3.Connection):
            self._ensure_tables(self.memory)
            yield self.memory
            return

        mem_conn = getattr(self.memory, "_conn", None)
        db_path = getattr(self.memory, "db_path", None)

        if str(db_path) == ":memory:" and mem_conn is not None:
            self._ensure_tables(mem_conn)
            yield mem_conn
            return

        if db_path is not None:
            path_str = str(db_path)
            is_uri = path_str.startswith("file:") or "?mode=memory" in path_str
            conn = sqlite3.connect(path_str, check_same_thread=False, uri=is_uri)
            try:
                self._ensure_tables(conn)
                yield conn
            finally:
                conn.close()
            return

        if mem_conn is not None:
            self._ensure_tables(mem_conn)
            yield mem_conn
            return

        raise RuntimeError("Unable to resolve active SQLite connection from EternalMemory")

    @staticmethod
    def _ensure_tables(conn: sqlite3.Connection) -> None:
        """Ensure the foundational EternalMemory conversation schema exists."""
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

        if not latest_preferences:
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
        # 1. Exact or substring match of tool name
        if t_lower in message_lower:
            return True

        # 2. Tokenized component matching (e.g. "launch_app" -> "launch", "app")
        tokens = [tok for tok in t_lower.replace("-", "_").split("_") if len(tok) >= 3]
        if any(tok in message_lower for tok in tokens):
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
                if any(str(tag).lower() in message_lower for tag in task_tags if len(str(tag)) >= 3):
                    return True
                category = getattr(meta, "category", "") or ""
                if category and len(category) >= 3 and category.lower() in message_lower:
                    return True

        # 4. Parameter value relevance
        if pref_data:
            params = pref_data.get("preferred_params") or {}
            for val in params.values():
                val_str = str(val).strip().lower()
                if len(val_str) >= 3 and val_str in message_lower:
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
