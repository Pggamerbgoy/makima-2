"""
Makima v7.1 — Agent Guardrails

Wraps every agent task with hard limits: wall time and tool-call count.

Note: token-budget/cost enforcement was intentionally removed (2026-07-18) —
there's no paid API budget being metered day-to-day (Groq free tier is the
primary backend), so a token *limit* added friction without real value.
Token usage is still tracked per-agent as telemetry (see base_agent.py's
get_execution_stats()) but nothing is enforced against it.

On a real limit hit: the agent self-cancels (raises GuardrailExceeded from
inside _use_tool()), the orchestrator saves the partial result as a draft
note (never discards work), and emits agent_guardrail_hit with the real
reason. Wall-time is enforced separately via asyncio.wait_for() in the
orchestrator, since it needs to interrupt agent.execute() from the outside.
"""
from __future__ import annotations
import hashlib
import json
import logging
from collections import deque

logger = logging.getLogger("makima.agent_guardrails")


class GuardrailExceeded(Exception):
    """
    Raised by an agent (via BaseAgent._use_tool) when a guardrail limit is
    hit mid-execution. Caught distinctly in AgentOrchestrator._run_agent()
    so the real reason (e.g. "tool_limit_exceeded") is reported instead of
    being reported as a generic error or a wall-time timeout.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class AgentGuardrails:
    """
    Configuration provider + limit checker for agent execution.

    Wall-time enforcement itself lives in AgentOrchestrator._run_agent()
    (via asyncio.wait_for), since only the orchestrator can interrupt an
    agent from the outside. This class is the single source of truth for
    *what* the limits are and whether a given usage count violates them.
    """

    def __init__(self, config: dict):
        self.config = config.get("agents", {})
        self.bg_limits = self.config.get("background_limits", {})
        self.int_limits = self.config.get("interactive_limits", {})
        # Per-agent recent tool-call history (fingerprints) for repetitive-loop
        # detection. Keyed by agent name because the Guardrails instance is
        # SHARED across all agents — a global deque would cross-contaminate.
        self._dup_history: dict[str, deque] = {}

    def _call_fingerprint(self, tool_name: str, params: dict) -> str:
        """Stable fingerprint of (tool, normalized params) for duplicate detection."""
        canonical = json.dumps(params, sort_keys=True, default=str)
        return tool_name + ":" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def record_tool_call(self, agent_name: str, tool_name: str, params: dict) -> None:
        """Record a tool invocation for this agent (keeps last 6 calls)."""
        dq = self._dup_history.setdefault(agent_name, deque(maxlen=6))
        dq.append(self._call_fingerprint(tool_name, params))

    def check_repetitive(self, agent_name: str) -> str:
        """
        Detect a stuck loop: the same (tool, params) 3x in a row means the
        LLM is repeating a call that keeps failing / not advancing state.
        Returns a violation reason string, or "" if not repetitive.
        """
        dq = self._dup_history.get(agent_name)
        if dq is not None and len(dq) >= 3 and len(set(dq)) == 1:
            return "repetitive_loop_detected"
        return ""

    def get_limits(self, is_interactive: bool = False) -> dict:
        limits = self.int_limits if is_interactive else self.bg_limits
        return {
            "max_wall_time_s": limits.get("max_wall_time_s", 300),
            "max_tool_calls": limits.get("max_tool_calls", 20),
        }

    def check_limits(self, tool_calls: int, is_interactive: bool = False) -> str:
        """
        Check if the tool-call limit is exceeded. Wall time is checked
        separately via asyncio.wait_for() in the orchestrator, since it
        needs to interrupt execution from the outside rather than being
        polled from inside the agent.

        Returns a violation reason string, or "" if within limits.
        """
        limits = self.get_limits(is_interactive)
        if tool_calls > limits["max_tool_calls"]:
            return "tool_limit_exceeded"
        return ""
