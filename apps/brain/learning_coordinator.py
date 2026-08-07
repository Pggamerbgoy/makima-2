"""Makima v7.2 — Learning Coordinator

The central self-learning hub. Receives 5 types of learning signals,
calls a fast LLM to extract actionable rules, stores them in LearningEngine,
and those rules are injected into future LLM calls — closing the loop.

Signal types:
1. negative_feedback   — thumbs down / "galat hai"
2. routing_correction  — user explicitly corrects which agent ran
3. regen_request       — "dobara karo" / "aur achha" = style dissatisfaction
4. tool_failure        — same tool fails repeatedly with same context
5. conversation_correction — "nahi, mera matlab tha..."

This file has ZERO external dependencies beyond the brain package.
All LLM calls are fire-and-forget (asyncio.create_task) so they never
block the main response pipeline. Zero-crash: every path wrapped in try/except.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("makima.learning_coordinator")

# How many tool failures in a row before we store an avoidance rule
_TOOL_FAILURE_THRESHOLD = 2

# Minimum interval between LLM analysis calls for the same signal type
# (prevents hammering the API if user spams thumbs down)
_MIN_ANALYSIS_INTERVAL_S = 8.0

# Fast, cheap model for rule extraction — not the main chat model
_ANALYSIS_TASK = "intent_classification"  # uses _GROQ_FAST_MODEL via ai_handler


_RULE_EXTRACTION_PROMPT = """You are Makima's self-learning analyst. A user interaction went wrong.
Analyze the failure and extract ONE short, actionable rule (max 20 words) that will prevent this mistake next time.

Failure type: {signal_type}
User message: {user_message}
What went wrong: {what_went_wrong}
Correct behavior: {correct_behavior}

Respond ONLY with this JSON — no other text:
{{
  "rule": "<20-word max rule that starts with a verb, e.g. 'Route X to Y when user says Z'>",
  "category": "<routing|style|tool|semantic|personality>",
  "keywords": ["word1", "word2", "word3"],
  "confidence": <0.5-0.95>
}}"""

_STYLE_EXTRACTION_PROMPT = """You are Makima's style analyst. The user asked to regenerate a response.
Extract what style preference they likely have.

Original response: {original_response}
User regeneration request: {regen_request}

Respond ONLY with this JSON:
{{
  "pref_key": "<response_length|response_language|response_tone|response_format>",
  "pref_value": "<short|long|hindi|english|hinglish|formal|casual|bullet|prose>",
  "confidence": <0.5-0.9>
}}"""


class LearningCoordinator:
    """Receives learning signals, analyzes them, and stores actionable rules."""

    def __init__(
        self,
        learning_engine: Any,
        ai_handler: Any,
    ) -> None:
        self.engine = learning_engine
        self.ai = ai_handler
        # Track tool failures: key = (agent, tool), value = [timestamps]
        self._tool_failure_log: dict[str, list[float]] = {}
        # Rate-limit per signal type
        self._last_analysis: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # ── Public signal receivers ───────────────────────────────────────────────

    async def on_negative_feedback(
        self,
        user_message: str,
        agent_response: str,
        agent_name: str = "unknown",
    ) -> None:
        """Called when user gives thumbs down or says 'galat hai / wrong'."""
        if not self._should_analyze("feedback"):
            return
        asyncio.create_task(self._analyze_and_store(
            signal_type="negative_feedback",
            user_message=user_message,
            what_went_wrong=f"Agent '{agent_name}' gave an unsatisfactory response",
            correct_behavior="Give a better, more accurate, or more appropriate response",
            extra_context={"original_response": agent_response[:300]},
        ))

    async def on_routing_correction(
        self,
        user_message: str,
        wrong_intent: str,
        correct_intent: str,
    ) -> None:
        """Called when the user corrects which agent/intent ran.
        e.g. 'ye browser me kyun gaya? research me jaana chahiye tha'
        """
        if not self._should_analyze(f"routing:{wrong_intent}"):
            return
        asyncio.create_task(self._analyze_and_store(
            signal_type="routing_correction",
            user_message=user_message,
            what_went_wrong=f"Message was routed to '{wrong_intent}' instead of '{correct_intent}'",
            correct_behavior=f"Route this type of message to '{correct_intent}'",
            category_hint="routing",
            confidence_boost=0.15,  # routing corrections are high-value signals
        ))

    async def on_regen_request(
        self,
        user_message: str,
        original_response: str,
        regen_phrase: str,
    ) -> None:
        """Called when user asks to redo/improve the response.
        Extracts a style preference rather than a routing/semantic rule.
        """
        if not self._should_analyze("regen"):
            return
        asyncio.create_task(self._analyze_style_and_store(
            original_response=original_response,
            regen_request=regen_phrase,
        ))

    async def on_tool_failure(
        self,
        agent_name: str,
        tool_name: str,
        user_message: str,
        error_text: str,
    ) -> None:
        """Called when a tool fails. Stores an avoidance rule after threshold."""
        key = f"{agent_name}:{tool_name}"
        async with self._lock:
            now = time.time()
            self._tool_failure_log.setdefault(key, [])
            # Keep only recent failures (last 5 min)
            self._tool_failure_log[key] = [
                t for t in self._tool_failure_log[key] if now - t < 300
            ]
            self._tool_failure_log[key].append(now)
            count = len(self._tool_failure_log[key])

        if count >= _TOOL_FAILURE_THRESHOLD:
            if not self._should_analyze(f"tool:{key}"):
                return
            asyncio.create_task(self._analyze_and_store(
                signal_type="tool_failure",
                user_message=user_message,
                what_went_wrong=f"Tool '{tool_name}' in agent '{agent_name}' failed {count}x: {error_text[:150]}",
                correct_behavior=f"Avoid using '{tool_name}' for this context, or use a fallback approach",
                category_hint="tool",
            ))
            # Reset counter after storing rule
            async with self._lock:
                self._tool_failure_log[key] = []

    async def on_conversation_correction(
        self,
        original_user_message: str,
        correction_message: str,
    ) -> None:
        """Called when user corrects a misunderstanding mid-conversation.
        e.g. 'nahi, mera matlab X tha, Y nahi'
        """
        if not self._should_analyze("correction"):
            return
        asyncio.create_task(self._analyze_and_store(
            signal_type="conversation_correction",
            user_message=original_user_message,
            what_went_wrong="Makima misunderstood the user's intent or meaning",
            correct_behavior=f"Understand that: {correction_message[:200]}",
            category_hint="semantic",
        ))

    # ── Internal analysis workers (fire-and-forget tasks) ────────────────────

    def _should_analyze(self, key: str) -> bool:
        """Rate-limit analysis calls so we don't spam the API."""
        now = time.time()
        last = self._last_analysis.get(key, 0.0)
        if now - last < _MIN_ANALYSIS_INTERVAL_S:
            return False
        self._last_analysis[key] = now
        return True

    async def _analyze_and_store(
        self,
        signal_type: str,
        user_message: str,
        what_went_wrong: str,
        correct_behavior: str,
        category_hint: str = "",
        confidence_boost: float = 0.0,
        extra_context: dict | None = None,
    ) -> None:
        """Call LLM to extract an actionable rule, then store it."""
        if not self.ai or not self.engine:
            return
        try:
            prompt = _RULE_EXTRACTION_PROMPT.format(
                signal_type=signal_type,
                user_message=(user_message or "")[:200],
                what_went_wrong=what_went_wrong[:300],
                correct_behavior=correct_behavior[:200],
            )
            response = await asyncio.wait_for(
                self.ai.generate(
                    messages=[{"role": "user", "content": prompt}],
                    task=_ANALYSIS_TASK,
                    require_json=True,
                    temperature=0.2,
                    max_tokens=120,
                ),
                timeout=6.0,
            )
            parsed = self.ai.try_parse_json(response.text if response else "")
            if not parsed or not parsed.get("rule"):
                return

            rule_text = parsed["rule"].strip()
            category = parsed.get("category") or category_hint or "general"
            keywords = parsed.get("keywords", [])
            confidence = min(0.95, float(parsed.get("confidence", 0.7)) + confidence_boost)

            if len(rule_text) < 5:
                return

            rule_id = await self.engine.store_rule(
                category=category,
                rule_text=rule_text,
                keywords=keywords,
                confidence=confidence,
                source=signal_type,
            )
            logger.info(
                "LearningCoordinator: new rule stored [%s] cat=%s conf=%.2f — %s",
                rule_id[:8] if rule_id else "?",
                category,
                confidence,
                rule_text[:60],
            )
        except asyncio.TimeoutError:
            logger.debug("LearningCoordinator: rule extraction timed out for signal=%s", signal_type)
        except Exception as e:
            logger.error("LearningCoordinator._analyze_and_store failed: %s", e)

    async def _analyze_style_and_store(
        self,
        original_response: str,
        regen_request: str,
    ) -> None:
        """Extract a style preference from a regen request and store it."""
        if not self.ai or not self.engine:
            return
        try:
            prompt = _STYLE_EXTRACTION_PROMPT.format(
                original_response=(original_response or "")[:300],
                regen_request=(regen_request or "")[:150],
            )
            response = await asyncio.wait_for(
                self.ai.generate(
                    messages=[{"role": "user", "content": prompt}],
                    task=_ANALYSIS_TASK,
                    require_json=True,
                    temperature=0.1,
                    max_tokens=80,
                ),
                timeout=5.0,
            )
            parsed = self.ai.try_parse_json(response.text if response else "")
            if not parsed or not parsed.get("pref_key") or not parsed.get("pref_value"):
                return

            await self.engine.set_style_pref(
                key=parsed["pref_key"],
                value=parsed["pref_value"],
                confidence=float(parsed.get("confidence", 0.65)),
            )
            logger.info(
                "LearningCoordinator: style pref updated — %s=%s",
                parsed["pref_key"],
                parsed["pref_value"],
            )
        except asyncio.TimeoutError:
            logger.debug("LearningCoordinator: style analysis timed out")
        except Exception as e:
            logger.error("LearningCoordinator._analyze_style_and_store failed: %s", e)
