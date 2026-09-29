"""
Makima OS — BaseAgent Compatibility Shim.
Provides backward compatibility for benchmark scripts and semantic verification tests.
"""
from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("makima.agents.base")


class BaseAgent:
    """Compatibility base agent implementing semantic compatibility and guardrail gates."""

    AGENT_NAME: str = "base_agent"

    def __init__(
        self,
        ai_handler: Any = None,
        tool_registry: Any = None,
        eternal_memory: Any = None,
        personality: Any = None,
        ws_broadcast: Any = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.ai = ai_handler
        self.tool_registry = tool_registry
        self.memory = eternal_memory
        self.personality = personality
        self.ws_broadcast = ws_broadcast
        self.config = config or {}
        self._current_context: dict[str, Any] = {}
        self._current_agent_task: Any = None

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return "executed"

    async def _pre_tool_gate(
        self,
        tool_name: str,
        params: dict[str, Any],
        context: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """Hook & Generic Semantic Compatibility Gate (Fallback & Security).

        Evaluates proposed tool execution against:
        1. Subclass security gates.
        2. Generic Semantic Compatibility (grounded polarity, desired state, negative constraints).

        Returns (blocked, reason). When blocked=True the tool is NOT executed.
        """
        ctx = context or getattr(self, "_current_context", {}) or {}
        agent_task = ctx.get("agent_task") or getattr(self, "_current_agent_task", None)

        if ctx.get("grounded_slots") or agent_task:
            slots = dict(ctx.get("grounded_slots") or {})
            if agent_task and hasattr(agent_task, "parameters") and agent_task.parameters:
                slots.update(agent_task.parameters)

            if isinstance(slots, dict) or agent_task:
                cap = None
                if self.tool_registry and hasattr(self.tool_registry, "get_capability"):
                    cap = self.tool_registry.get_capability(tool_name, params)

                polarity = slots.get("polarity", "ALLOW")
                target = str(
                    slots.get("target")
                    or (getattr(agent_task, "target_entity", "") if agent_task else "")
                    or ""
                ).strip().lower()
                desired_state = str(slots.get("desired_state") or "").strip().lower()
                constraints = list(slots.get("constraints", []) or [])
                if agent_task and hasattr(agent_task, "negative_constraints") and agent_task.negative_constraints:
                    for nc in agent_task.negative_constraints:
                        if nc not in constraints:
                            constraints.append(nc)
                param_str = str(params).lower()

                def _matches_entity(entity: str, text: str) -> bool:
                    if not entity or not text:
                        return False
                    entity_l = entity.lower().strip()
                    text_l = text.lower().strip()
                    if entity_l in text_l or text_l in entity_l:
                        return True

                    entity_clean = re.sub(r"[\s._\-]+", "", entity_l.replace(".exe", ""))
                    text_clean = re.sub(r"[\s._\-]+", "", text_l.replace(".exe", ""))
                    if entity_clean and text_clean and (entity_clean in text_clean or text_clean in entity_clean):
                        return True

                    tokens = [t for t in re.split(r"[^a-z0-9]+", text_l) if len(t) >= 3]
                    for tok in tokens:
                        if len(tok) >= 3 and len(entity_l) >= 3:
                            ratio = difflib.SequenceMatcher(None, entity_l, tok).ratio()
                            if ratio >= 0.78:
                                return True
                    return False

                # Rule 0: Specific Negative Constraints
                for c in constraints:
                    if isinstance(c, str):
                        c_clean = c.lower().strip()
                        excluded_entity = ""
                        if c_clean.startswith("not_"):
                            excluded_entity = c_clean[4:].strip()
                        elif c_clean.startswith("not "):
                            excluded_entity = c_clean[4:].strip()
                        elif c_clean.startswith("avoid "):
                            excluded_entity = c_clean[6:].strip()
                        elif c_clean.startswith("keep ") and " running" in c_clean:
                            excluded_entity = c_clean[5:].replace(" running", "").strip()

                        if excluded_entity and (_matches_entity(excluded_entity, param_str) or _matches_entity(excluded_entity, tool_name)):
                            return True, f"[BLOCKED by Semantic Compatibility Gate]: Execution matches negative constraint '{c}' (target: '{excluded_entity}')."

                # Rule 1: Negative Constraint / Deny Polarity
                if str(polarity).upper() in ("DENY", "NEGATIVE"):
                    is_affirmative = (
                        (getattr(cap, "polarity", "") == "affirmative")
                        if cap
                        else bool(target and (_matches_entity(target, param_str) or _matches_entity(target, tool_name)))
                    )
                    if is_affirmative:
                        if (
                            not target
                            or _matches_entity(target, param_str)
                            or _matches_entity(target, tool_name)
                            or (cap and (_matches_entity(target, getattr(cap, "domain", "")) or _matches_entity(target, getattr(cap, "target_type", ""))))
                        ):
                            op_name = getattr(cap, "operation", "execute") if cap else "execute"
                            tgt_name = target or (getattr(cap, "domain", "") if cap else "resource")
                            return True, f"[BLOCKED by Semantic Compatibility Gate]: User explicitly requested NOT to {op_name} '{tgt_name}'."

                # Rule 2: Desired State Inversion Protection
                if desired_state and cap:
                    trans = getattr(cap, "state_transition", ("", ""))
                    to_state = str(trans[1] if len(trans) > 1 else "").strip().lower()
                    if desired_state in ("not_playing", "stopped", "paused", "closed", "iconic", "sleep", "off", "terminated"):
                        if to_state in ("playing", "running", "open", "maximized", "foreground") or getattr(cap, "polarity", "") == "affirmative":
                            return True, f"[BLOCKED by Semantic Compatibility Gate]: Proposed tool '{tool_name}' transitions state to '{to_state or 'active'}', which contradicts desired state '{desired_state}'."
                    elif desired_state in ("playing", "running", "open", "maximized", "foreground"):
                        if to_state in ("stopped", "paused", "closed", "off", "terminated") or getattr(cap, "polarity", "") == "negative":
                            return True, f"[BLOCKED by Semantic Compatibility Gate]: Proposed tool '{tool_name}' transitions state to '{to_state or 'inactive'}', which contradicts desired state '{desired_state}'."

        return False, ""
