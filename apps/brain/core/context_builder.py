"""
Makima OS v9.0 — Canonical Context Builder
Tiered, structured context construction: Tier 1 (Core/Safety) -> Tier 2 (Domain OS/User) -> Tier 3 (Memory/History) -> Tier 4 (Rules).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("makima.context_builder")


class ContextBuilder:
    """
    Canonical context and system prompt assembler for all agent executions.
    Decouples prompt assembly from BaseAgent and guarantees zero hardcoding.
    Enforces domain-specific context tier slicing and learned rule scoping.
    """

    DEFAULT_DOMAIN_TIERS: dict[str, set[str]] = {
        "system": {"tier_2_os", "tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_screen", "tier_3_clipboard", "tier_3_memory", "tier_4_rules"},
        "browser": {"tier_2_os", "tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_screen", "tier_3_memory", "tier_4_rules"},
        "media": {"tier_2_os", "tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_memory", "tier_4_rules"},
        "code": {"tier_2_os", "tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_document", "tier_3_memory", "tier_4_rules"},
        "document": {"tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_document", "tier_3_memory", "tier_4_rules"},
        "messaging": {"tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_memory", "tier_3_clipboard", "tier_4_rules"},
        "devops": {"tier_2_os", "tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_memory", "tier_4_rules"},
        "research": {"tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_memory", "tier_4_rules"},
        "memory": {"tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_memory", "tier_4_rules"},
        "automation": {"tier_2_os", "tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_memory", "tier_4_rules"},
        "data_analyst": {"tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_document", "tier_3_memory", "tier_4_rules"},
        "creative": {"tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_document", "tier_3_memory", "tier_4_rules"},
        "security": {"tier_2_os", "tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_memory", "tier_4_rules"},
        "coordinator": {"tier_2_os", "tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_memory", "tier_4_rules"},
        "general": {"tier_2_os", "tier_2_user_persona", "tier_3_slots", "tier_3_artifacts", "tier_3_memory", "tier_4_rules"},
    }

    def __init__(self, workspace_root: Optional[str] = None) -> None:
        self.workspace_root = workspace_root or os.path.abspath(os.getcwd())

    def build_system_prompt(
        self,
        agent_name: str,
        system_persona: str = "",
        context: Optional[dict[str, Any]] = None,
        extra_system: str = "",
        required_context_tiers: Optional[list[str]] = None,
    ) -> str:
        """Construct tiered system prompt adhering to structured domain requirements."""
        ctx = dict(context or {})
        agent_task = ctx.get("agent_task")
        domain_raw = (getattr(agent_task, "domain", None) if agent_task else None) or agent_name.replace("_agent", "").lower()
        domain = domain_raw.strip().lower()

        # Resolve active tiers from explicit declaration or domain defaults
        if required_context_tiers:
            active_tiers = set(required_context_tiers)
        else:
            active_tiers = set(self.DEFAULT_DOMAIN_TIERS.get(domain, self.DEFAULT_DOMAIN_TIERS["general"]))


        sections: list[str] = []

        # ── Tier 1: Core Persona & Safety Bounds (Always Active) ─────────────
        base_persona = (system_persona or f"You are Makima's {agent_name}.").strip()
        sections.append(base_persona)

        # Real-World Temporal Anchor (Always Active)
        from datetime import datetime
        now_dt = datetime.now()
        temporal_anchor = (
            f"[CURRENT REAL-WORLD DATE & TIME: {now_dt.strftime('%A, %B %d, %Y - %I:%M %p')} (Current Year: {now_dt.year})]\n"
            f"- Today is {now_dt.strftime('%B %d, %Y')}. The current year is {now_dt.year}.\n"
            f"- Training cutoff is historical (2024). Never assume current year is 2024 or 2025.\n"
            f"- Any queries for live news, headlines, current events, or real-time status must be grounded via live web search."
        )
        sections.append(temporal_anchor)

        if extra_system and extra_system.strip():
            sections.append(extra_system.strip())

        # ── Tier 2A: Local OS Environment (Domain-on-demand) ────────────────
        needs_os = (
            "tier_2_os" in active_tiers
            or "os_state" in active_tiers
            or ctx.get("force_os") is True
        )
        if needs_os or ctx.get("os_state"):
            try:
                from .world_state import get_world_state
                from .known_folders import resolve_known_folder
                ws = get_world_state()
                # OneDrive-aware real Desktop (expanduser lies under KFM)
                desktop_path = resolve_known_folder("desktop") or os.path.expanduser("~/Desktop")
                os_lines = [
                    "[LOCAL OS ENVIRONMENT & RUNTIME STATE]",
                    f"- OS Platform: win32 (Windows)",
                    f"- Workspace Root: {self.workspace_root}",
                    f"- User Desktop: {desktop_path}",
                ]
                fg_title = ws.get_foreground_window()
                if fg_title:
                    os_lines.append(f"- Active Foreground Window: \"{fg_title[:60]}\"")
                win_count = ws.get_window_count() if hasattr(ws, "get_window_count") else 0
                if win_count > 0:
                    os_lines.append(f"- Open Application Windows: {win_count}")
                cpu_val = ws.cpu_avg()
                if cpu_val is not None and cpu_val > 0:
                    os_lines.append(f"- CPU Usage (5m avg): {cpu_val:.1f}%")
                bat = ws.get_battery_status()
                if bat.get("has_battery"):
                    os_lines.append(f"- Battery: {bat.get('percent')}% ({'Plugged in' if bat.get('power_plugged') else 'On Battery'})")

                sections.append("\n".join(os_lines))

                try:
                    from ..mental_state import get_mental_state_detector
                    ms_prompt = get_mental_state_detector().get_prompt_context()
                    if ms_prompt:
                        sections.append(ms_prompt)
                except Exception as ms_err:
                    logger.debug("[context_builder] Mental state injection error: %s", ms_err)
            except Exception as os_err:
                logger.debug("[context_builder] OS state injection error: %s", os_err)

        # ── Tier 2B: User Persona & Profile Traits ─────────────────────────
        user_persona = ctx.get("user_persona")
        if ("tier_2_user_persona" in active_tiers or "user_persona" in active_tiers or ctx.get("force_user_persona")) and user_persona and isinstance(user_persona, dict) and user_persona:
            try:
                persona_lines = ["[USER PROFILE & PERSONA / IDENTITY (Learned from chat)]"]
                identity = user_persona.get("user_identity") or {}
                uname = identity.get("user_name") or user_persona.get("user_name")
                if uname:
                    persona_lines.append(f"• User's Name: {uname}")
                ulang = identity.get("preferred_language")
                if ulang:
                    persona_lines.append(f"• Preferred Language: {ulang}")

                def _format_dict_recursive(d: dict[str, Any], indent: int = 2) -> list[str]:
                    lines = []
                    pad = " " * indent
                    for k, v in d.items():
                        if k in ("user_identity", "user_name") or not v:
                            continue
                        key_title = str(k).replace("_", " ").title()
                        if isinstance(v, dict) and v:
                            lines.append(f"{pad}• {key_title}:")
                            lines.extend(_format_dict_recursive(v, indent + 2))
                        elif isinstance(v, list) and v:
                            items_str = ", ".join(str(x) for x in v[:10])
                            lines.append(f"{pad}• {key_title}: {items_str}")
                        elif v is not None and str(v).strip():
                            lines.append(f"{pad}• {key_title}: {v}")
                    return lines

                formatted_lines = _format_dict_recursive(user_persona)
                if formatted_lines:
                    persona_lines.extend(formatted_lines)
                if len(persona_lines) > 1:
                    sections.append("\n".join(persona_lines))
            except Exception as persona_err:
                logger.debug("[context_builder] Persona injection error: %s", persona_err)

        # ── Tier 2C: Live World-State Grounded Entities (Pre-Planning) ────────
        grounded_entity = ctx.get("grounded_entity")
        if grounded_entity and isinstance(grounded_entity, dict):
            if grounded_entity.get("resolved") and grounded_entity.get("entity"):
                etype = str(grounded_entity.get("entity_type", "entity")).replace("_", " ").title()
                eval_str = grounded_entity.get("entity")
                sections.append(
                    f"[LIVE WORLD-STATE GROUNDED ENTITIES]\n"
                    f"• {etype}: \"{eval_str}\" (Resolved deterministically via live OS probes — use this target directly)"
                )
            elif grounded_entity.get("resolved") is False:
                etype = str(grounded_entity.get("entity_type", "entity")).replace("_", " ").title()
                sections.append(
                    f"[LIVE WORLD-STATE GROUNDED ENTITIES]\n"
                    f"• {etype}: None detected (Probe confirmed no active application matches this referent — do not hallucinate an entity)"
                )


        # ── Tier 3A: Predecessor Subtask Outputs & Grounded Command Slots ────
        if ctx.get("grounded_slots") and isinstance(ctx["grounded_slots"], dict):
            slots_repr = ", ".join(f"{k}='{v}'" for k, v in ctx["grounded_slots"].items() if v)
            if slots_repr:
                sections.append(f"[GROUNDED COMMAND SLOTS]\n{slots_repr}")

        # Always preserve predecessor task artifacts for multi-step / DAG tasks
        prev_res = ctx.get("_previous_results")
        ctx_artifacts = ctx.get("context_artifacts") or ctx.get("artifacts")
        
        if isinstance(ctx_artifacts, dict) and ctx_artifacts:
            art_items = []
            if "files" in ctx_artifacts or "created_files" in ctx_artifacts:
                f_list = ctx_artifacts.get("files") or ctx_artifacts.get("created_files") or []
                if f_list:
                    art_items.append(f"• Created Files: {', '.join(str(f) for f in f_list)}")
            if "urls" in ctx_artifacts or "extracted_urls" in ctx_artifacts:
                u_list = ctx_artifacts.get("urls") or ctx_artifacts.get("extracted_urls") or []
                if u_list:
                    art_items.append(f"• Extracted URLs: {', '.join(str(u) for u in u_list)}")
            for k, v in ctx_artifacts.items():
                if k not in ("files", "created_files", "urls", "extracted_urls") and v:
                    art_items.append(f"• {k}: {v}")
            if art_items:
                sections.append("[CONTEXT ARTIFACTS]\n" + "\n".join(art_items))

        if isinstance(prev_res, dict) and prev_res:
            res_items = []
            for subtask_id, result in prev_res.items():
                if result and not str(result).startswith("[Failed]"):
                    clean_res = str(result).strip()
                    if len(clean_res) > 2000:
                        clean_res = clean_res[:2000] + "\n[...truncated for context limit]"
                    res_items.append(f"• [{subtask_id} Output]: {clean_res}")
            if res_items:
                sections.append(
                    "[PREVIOUS SUBTASK OUTPUTS & ARTIFACTS — PREVIOUS SUBTASK ARTIFACTS — Consume these exact paths/data directly without re-fetching]:\n"
                    + "\n".join(res_items)
                )

        # ── Tier 3B: Episodic Long-Term Memory Results ───────────────────────
        mem_results = ctx.get("memory_results")
        if mem_results and isinstance(mem_results, list):
            mem_text = "\n".join(f"• {str(m).replace('<system>', '').replace('</system>', '').strip()}" for m in mem_results if m)
            if mem_text:
                sections.append(f"[RELEVANT RECALLED MEMORY]\n{mem_text}")

        # ── Tier 3C: Environment Modalities (Screen, Clipboard, Document) ────
        if ("tier_3_screen" in active_tiers or "screen" in active_tiers or ctx.get("include_screen") or ctx.get("force_screen")) and ctx.get("screen_context"):
            sections.append(f"[CURRENT SCREEN]\n{ctx['screen_context']}")

        if ("tier_3_clipboard" in active_tiers or "clipboard" in active_tiers or ctx.get("include_clipboard") or ctx.get("force_clipboard")) and ctx.get("clipboard"):
            sections.append(f"[CLIPBOARD]\n{ctx['clipboard']}")

        if ("tier_3_document" in active_tiers or "document" in active_tiers or ctx.get("include_document") or ctx.get("force_document")) and ctx.get("document"):
            sections.append(f"[DOCUMENT]\n{ctx['document']}")

        # ── Tier 4: Learned Behavioral & Procedural Rules (Domain Scoped) ────
        behavior_rules = ctx.get("behavior_rules")
        if behavior_rules and isinstance(behavior_rules, list):
            rule_texts = []
            for r in behavior_rules:
                if isinstance(r, dict):
                    r_dom = str(r.get("domain") or r.get("category") or "general").lower().strip()
                    # Domain scoping: global rules reach all agents; domain-specific rules reach matching domain
                    if r_dom in ("general", "global", "", "all", "tool", "semantic", "style", "personality", "reflexion", "feedback", "routing", domain):
                        txt = r.get("rule_text") or r.get("rule") or r.get("text")
                        if txt:
                            rule_texts.append(f"• {str(txt).replace('<system>', '').replace('</system>', '').strip()}")
                elif isinstance(r, str) and r.strip():
                    rule_texts.append(f"• {r.replace('<system>', '').replace('</system>', '').strip()}")
                if len(rule_texts) >= 8:
                    break

            if rule_texts:
                sections.append("[LEARNED BEHAVIORAL RULES - MUST FOLLOW]\n" + "\n".join(rule_texts))

        # ── Tier 4B: Past Failure Lessons (ReflexionEngine — Shinn et al. 2023) ───
        past_lessons = ctx.get("past_failure_lessons")
        if past_lessons:
            if isinstance(past_lessons, list):
                lesson_lines = [str(l).strip() for l in past_lessons if str(l).strip()]
                if lesson_lines:
                    sections.append("[PAST FAILURE LESSONS — APPLY NOW]\n" + "\n".join(lesson_lines))
            elif isinstance(past_lessons, str) and past_lessons.strip():
                sections.append(f"[PAST FAILURE LESSONS — APPLY NOW]\n{past_lessons.strip()}")

        # ── Tier 4C: Prior Reflexion Adaptations (Episodic Failure Memory) ─────
        reflections = ctx.get("reflections")
        if reflections and isinstance(reflections, list):
            ref_lines = []
            for ref in reflections[:4]:
                if isinstance(ref, dict):
                    crit = ref.get("critique") or ref.get("trajectory", "")
                    strat = ref.get("next_strategy") or ref.get("rule_text", "")
                    if crit and strat:
                        ref_lines.append(f"• Past Failure: {crit}\n  Adapted Strategy: {strat}")
                elif isinstance(ref, str) and ref.strip():
                    ref_lines.append(f"• {ref.strip()}")
            if ref_lines:
                sections.append("[PRIOR REFLEXION ADAPTATIONS - DO NOT REPEAT PAST MISTAKES]\n" + "\n".join(ref_lines))

        # ── Cognitive Discipline Block (Always Injected) ──────────────────────
        sections.append(
            "[COGNITIVE REASONING & EXECUTION PROTOCOL]\n"
            "1. Structured Thinking: Before calling any tool or producing a final response, perform concise step-by-step reasoning in a <thinking>...</thinking> block.\n"
            "2. Tool Honesty: NEVER state an action succeeded unless verified by real tool output. If a tool fails, state the exact reason.\n"
            "3. Clean User Output: Never leak internal plumbing tags, function signatures, or raw JSON envelopes in user-facing replies."
        )

        return "\n\n".join(sections)

    def build_messages(
        self,
        user_message: str,
        agent_name: str,
        system_persona: str = "",
        context: Optional[dict[str, Any]] = None,
        extra_system: str = "",
        required_context_tiers: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Assemble complete OpenAI/Anthropic messages list."""
        ctx = dict(context or {})
        system_prompt = self.build_system_prompt(
            agent_name=agent_name,
            system_persona=system_persona,
            context=ctx,
            extra_system=extra_system,
            required_context_tiers=required_context_tiers,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        # Inject recent multi-turn conversational history — but strip OUR OWN
        # plumbing/status lines (they leak into prompts and get echoed back to
        # users as fake output), cap turns, and truncate per-turn bloat.
        _STOP_PREFIXES = (
            "youtube:", "spotify:", "[Snapshot:", "[Manifest:", "⚙ tools dispatched",
        )

        def _is_artifact_line(line: str) -> bool:
            s = line.strip()
            if not s:
                return False
            low = s.lower()
            if "⚙ tools dispatched" in low or "calling tools:" in low:
                return True
            return any(s.startswith(p) for p in _STOP_PREFIXES)

        history = ctx.get("history")
        if history and isinstance(history, list):
            for turn in history[-30:]:
                if isinstance(turn, dict) and "role" in turn and "content" in turn:
                    cleaned = "\n".join(
                        ln for ln in str(turn["content"] or "").splitlines()
                        if not _is_artifact_line(ln)
                    ).strip()
                    if len(cleaned) > 2500:
                        cleaned = cleaned[:2500] + "…"
                    if cleaned:
                        messages.append({"role": turn["role"], "content": cleaned})

        # Inject user turn
        messages.append({"role": "user", "content": user_message})
        return messages
