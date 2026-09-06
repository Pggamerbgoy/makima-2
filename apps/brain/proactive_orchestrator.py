"""
Makima v9.x — Proactive Action Orchestrator
Location: apps/brain/proactive_orchestrator.py

Turns Makima from a reactive-only assistant into a proactive one that can act
WITHOUT being asked — safely, transparently, and within hard limits.

Architecture:
  Signals ──▶ Decision (cheap LLM call) ──▶ Risk classification ──▶ Execution

Signal sources (all existing infra, zero duplication):
  1. Predictive habit patterns   — learning_engine.get_predictive_suggestion()
     (time_patterns table; this module also WRITES habits by observing recent
      kernel task outcomes, closing the loop that was previously write-only)
  2. Recent task history         — kernel.event_store ("task_outcome" events)

Safety model (industry-standard risk-tiered autonomy):
  mode: "off" | "suggest" | "auto"        (persisted to configs/proactive_state.json)
    - off     : no proactive behavior at all
    - suggest : only surfaces suggestions as text (💡 style), executes nothing
    - auto    : low-risk reversible actions execute automatically;
                dangerous/irreversible actions ALWAYS raise a confirmation card

  Risk tiers (deterministic keyword classifier wins over any LLM hint):
    notify    — pure observation/information, never touches the system
    safe      — reversible low-risk actions (open app, reminder, organize copy,
                drafts, research summaries)
    dangerous — app close/kill, file deletion, shutdown/restart, sending real
                messages, payments, credentials → confirmation required ALWAYS

Hard guardrails:
  - max_actions_per_hour / max_actions_per_day rate limits
  - quiet hours (no proactive execution at night)
  - dedup window (never repeat the same proactive action too soon)
  - every action audited into the kernel event_store AND broadcast to the UI
  - zero-crash: one bad tick never kills the loop
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections import deque
from pathlib import Path
from enum import IntEnum
from typing import Any, Dict, Optional

logger = logging.getLogger("makima.proactive")

# ─── 5-Tier Autonomy & Graded Trust ───────────────────────────────────────────

class AutonomyLevel(IntEnum):
    """5-Tier Graded Autonomy hierarchy for predictive actions."""
    LEVEL_0_OBSERVATIONAL = 0   # Silent passive telemetry/audit only (no UI)
    LEVEL_1_AMBIENT_CUE = 1     # Subtle ambient cue / status indicator
    LEVEL_2_SUGGESTION_CARD = 2 # Interactive suggestion card (click to approve)
    LEVEL_3_REVERSIBLE_AUTO = 3 # Auto-execute reversible non-destructive actions
    LEVEL_4_FULL_AUTONOMOUS = 4 # Direct autonomous execution for routine proven habits


# ─── Defaults & Constants ─────────────────────────────────────────────────────

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "mode": "suggest",           # off | suggest | auto
    "autonomy_level": AutonomyLevel.LEVEL_2_SUGGESTION_CARD,
    "interval_s": 300,            # decision tick every 5 minutes
    "max_actions_per_hour": 3,
    "max_actions_per_day": 20,
    "quiet_start_hour": 23,       # inclusive
    "quiet_end_hour": 7,          # exclusive
    "dedup_window_s": 3600,       # same proposal not repeated within 1h
    "confirm_timeout_s": 90,      # dangerous-action card timeout
}

VALID_MODES = ("off", "suggest", "auto")
VALID_CATEGORIES = ("system", "research", "schedule", "files")
DEFAULT_ALLOWED = ["system", "research", "schedule", "files"]

# Deterministic danger classifier — conservative by design. Any match here
# forces tier="dangerous" regardless of what the LLM suggested.
_DANGEROUS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # App/process termination
        r"\b(close|quit|exit|terminate|kill|force[- ]?stop)s?\b",
        # Destructive file ops
        r"\b(delet|remov|uninstall|format|wipe|shred|trash|recycle|purge)\w*\b",
        r"\b(rm|del)\s+\S",
        # Power/session control
        r"\b(shutdown|restart|reboot|hibernate|log\s?-?off|logout|lock)\b",
        # Outbound communication to real people
        r"\b(send|reply|forward|post|tweet|publish|email)\w*\b",
        # Money & credentials
        r"\b(pay|payment|purchase|buy|checkout|transfer|order)s?\b",
        r"\b(password|credential|api[ -]?key|secret|token)\b",
        # System-level changes
        r"\b(registry|firewall|uac|elevate|sudo|chmod|driver)\b",
        r"\b(install|update|upgrade)\s+\w+",
        # Prompt injection / jailbreak attempts
        r"\b(ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?|system\s+override|jailbreak|disregard\s+(?:all\s+)?prior)\b",
    )
)

_NOTIFY_HINT = ("notify", "observation", "info", "information")
_SAFE_HINT = ("safe", "low", "low_risk", "low-risk")

_DECISION_PROMPT = """You are Makima's proactive-decision engine. Based on the user's habits and \
recent activity, decide whether Makima should proactively DO something right now (without being asked).

User's habitual suggestion at this time slot: "{signal}"
Recent tasks the user ran: {recent}
Available agents: {agents}
Current local time: {now}

Rules:
- "act" is false unless you are confident the action is genuinely useful RIGHT NOW. When unsure, do not act.
- Prefer read-only/reversible actions (research summaries, reminders, opening apps, organizing copies, drafts).
- NEVER propose: closing/killing apps, deleting anything, shutting down/restarting, sending real messages \
to contacts, payments, credential changes. Those require explicit user request.
- "agent" must be exactly one of the available agents.
- "task_description" must be a complete standalone instruction for that agent (max 25 words).

Respond ONLY with this JSON:
{{"act": true/false, "category": "system|research|schedule|files", "agent": "<agent_name>", \
"task_description": "<instruction>", "risk_hint": "notify|safe|dangerous", "rationale": "<max 15 words>"}}"""


class ProactiveOrchestrator:
    """Signal-driven proactive action engine with risk-tiered autonomy."""

    def __init__(
        self,
        config: Optional[dict] = None,
        ws_broadcast: Any = None,
        ai_handler: Any = None,
        learning_engine: Any = None,
        kernel: Any = None,
        orchestration_engine: Any = None,
        learning: Any = None,
        **kwargs: Any,
    ) -> None:
        raw_cfg = config.get("proactive", {}) if isinstance(config, dict) else {}
        self.cfg: Dict[str, Any] = {**DEFAULT_CONFIG, **{k: v for k, v in raw_cfg.items() if v is not None}}
        self.ws_broadcast = ws_broadcast
        self.ai = ai_handler
        # TODO: ReflexionEngine will handle this
        self.learning = None
        self.kernel = kernel
        self.orchestration_engine = orchestration_engine
        self.durable_task_engine = kwargs.get("durable_task_engine") or getattr(kernel, "durable_task_engine", None)

        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        self._background_tasks: set[asyncio.Task] = set()
        self.mode: str = str(self.cfg.get("mode", "suggest"))
        self._load_persisted_mode()

        # Guardrail state
        self._action_times: deque[float] = deque()          # executed action timestamps
        self._dedup: Dict[str, float] = {}                  # proposal_hash -> last ts
        self._last_outcome_event_id: int = 0                 # habit-loop cursor

        # Best-effort state file next to voice_config.json conventions
        self._state_path = Path(__file__).resolve().parents[2] / "configs" / "proactive_state.json"

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self.cfg.get("enabled", True):
            logger.info("ProactiveOrchestrator disabled by config — skipping start")
            return
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._loop(), name="proactive-loop")
        logger.info(
            "ProactiveOrchestrator started (mode=%s, interval=%ss, max/h=%.0f)",
            self.mode, self.cfg.get("interval_s"), float(self.cfg.get("max_actions_per_hour", 3)),
        )

    async def stop(self) -> None:
        self._running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        for t in list(self._background_tasks):
            if not t.done():
                t.cancel()
        self._background_tasks.clear()
        logger.info("ProactiveOrchestrator stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Mode management (UI kill-switch) ──────────────────────────────────────

    async def set_mode(self, mode: str, source: str = "user") -> dict:
        mode = str(mode or "").strip().lower()
        if mode not in VALID_MODES:
            return {"ok": False, "error": f"Invalid mode '{mode}'. Valid: {VALID_MODES}"}
        previous = self.mode
        self.mode = mode
        self._persist_mode()
        await self._emit_ws("autonomy_mode_changed", {
            "mode": mode, "previous": previous, "source": source,
        })
        logger.info("Autonomy mode changed: %s -> %s (%s)", previous, mode, source)
        return {"ok": True, "mode": mode, "previous": previous}

    def get_status(self) -> dict:
        return {
            "mode": self.mode,
            "running": self._running,
            "actions_last_hour": sum(1 for t in self._action_times if time.time() - t < 3600),
            "actions_today": sum(1 for t in self._action_times if time.time() - t < 86400),
            "config": {k: v for k, v in self.cfg.items()},
        }

    def _load_persisted_mode(self) -> None:
        try:
            path = Path(__file__).resolve().parents[2] / "configs" / "proactive_state.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                persisted = str(data.get("mode", "")).lower()
                if persisted in VALID_MODES:
                    self.mode = persisted
        except Exception as e:
            logger.debug("Failed to load proactive state: %s", e)

    def _persist_mode(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"mode": self.mode, "updated_at": time.time()}), encoding="utf-8"
            )
        except Exception as e:
            logger.debug("Failed to persist proactive mode: %s", e)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        interval = max(30.0, float(self.cfg.get("interval_s", 300)))
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Proactive tick failed: %s", e)
            await asyncio.sleep(interval)

    async def _tick(self) -> None:
        if self.mode == "off":
            return
        if self._in_quiet_hours():
            return

        # Check for scheduled durable task resumptions (Module 4)
        await self._check_durable_task_resumptions()

        # Causal Situational Rules Check (Battery, Mental State, Build Error)
        await self._check_causal_situational_triggers()

    async def _check_durable_task_resumptions(self) -> None:
        """Auto-resume tasks where resume_after <= now."""
        dte = getattr(self, "durable_task_engine", None)
        if not dte and self.kernel and hasattr(self.kernel, "durable_task_engine"):
            dte = self.kernel.durable_task_engine
        if not dte and self.orchestration_engine and hasattr(self.orchestration_engine, "durable_task_engine"):
            dte = self.orchestration_engine.durable_task_engine

        if not dte:
            return

        try:
            pending = await dte.list_pending_tasks()
            now = time.time()
            for cp in pending:
                if cp.resume_after and cp.resume_after <= now:
                    logger.info("[ProactiveOrchestrator] Auto-resuming scheduled durable task %s", cp.task_id)
                    resumed = await dte.resume_task(cp.task_id)
                    if resumed and self.orchestration_engine:
                        if hasattr(self.orchestration_engine, "run_autonomous_goal"):
                            task_coro = self.orchestration_engine.run_autonomous_goal(
                                task_id=cp.task_id,
                                goal=resumed.continuation_prompt,
                                conversation_id=resumed.reconstructed_context.get("conversation_id", "default_session"),
                                context=resumed.reconstructed_context,
                            )
                            bg_task = asyncio.create_task(task_coro)
                            self._background_tasks.add(bg_task)
                            bg_task.add_done_callback(self._background_tasks.discard)
        except Exception as exc:
            logger.debug("[ProactiveOrchestrator] Durable task resume check error: %s", exc)

    async def _check_causal_situational_triggers(self, world_state: Any = None) -> bool:
        """Evaluate real-time causal conditions: battery, cognitive load, build failure, gaming habits."""
        try:
            if world_state is not None:
                ws = world_state
            else:
                from .core.world_state import get_world_state
                ws = get_world_state()

            # 1. Low battery (< 18% and not plugged)
            bat = ws.get_battery_status()
            if bat.get("has_battery") and not bat.get("power_plugged") and float(bat.get("percent", 100)) <= 18.0:
                dedup_key = "causal_battery_critical"
                if self._dedup_allow(dedup_key):
                    desc = f"Battery critical at {bat['percent']}%. Plug in charger and save active work."
                    await self._handle_proactive_action("system_agent", desc, "Low battery warning", risk_hint="notify")
                    return True

            # 2. Mental Overwhelm / Window Thrashing
            ms = ws.get_mental_state()
            if ms.get("state") == "overwhelmed":
                dedup_key = "causal_mental_overwhelmed"
                if self._dedup_allow(dedup_key):
                    desc = "Rapid app switching detected. Would you like me to organize open windows or minimize distractions?"
                    await self._handle_proactive_action("system_agent", desc, "Cognitive load relief", risk_hint="notify")
                    return True

            # 3. Active Build / Terminal Error in foreground window
            from .agents.os_state import get_os_state
            fg_win = get_os_state().get_foreground_window().lower()
            if any(k in fg_win for k in ("build fail", "syntaxerror", "compilation error", "exception in")):
                dedup_key = f"causal_build_err_{hashlib.md5(fg_win.encode()).hexdigest()[:8]}"
                if self._dedup_allow(dedup_key):
                    desc = f"Build error detected in '{fg_win[:40]}'. Pull relevant diagnostics and documentation?"
                    await self._handle_proactive_action("research_agent", desc, "Build error assistance", risk_hint="notify")
                    return True

            return False
        except Exception as e:
            logger.debug("Causal triggers evaluation error: %s", e)
            return False

    async def _handle_proactive_action(self, agent: str, task_desc: str, rationale: str, risk_hint: str = "notify") -> None:
        """Route action according to 5-Tier Autonomy and safety classification."""
        autonomy = int(self.cfg.get("autonomy_level", AutonomyLevel.LEVEL_2_SUGGESTION_CARD))
        tier = self.classify_risk(task_desc, llm_hint=risk_hint)

        if autonomy == AutonomyLevel.LEVEL_0_OBSERVATIONAL:
            await self._audit("observed_only", agent=agent, description=task_desc, rationale=rationale, tier=tier)
            return

        if tier == "safe" and autonomy >= AutonomyLevel.LEVEL_3_REVERSIBLE_AUTO:
            await self._execute_proactive(agent, task_desc, rationale)
            return

        if autonomy <= AutonomyLevel.LEVEL_2_SUGGESTION_CARD or tier == "notify" or self.mode == "suggest":
            await self._broadcast_proposal(agent, task_desc, rationale, tier)
            return

        # Dangerous or high risk → require interactive confirmation
        approved = await self._request_confirmation(task_desc, rationale)
        if approved:
            await self._execute_proactive(agent, task_desc, rationale, confirmed=True)
        else:
            await self._audit("rejected", agent=agent, description=task_desc, rationale=rationale, tier=tier)

    # ── Signal collection ─────────────────────────────────────────────────────

    async def _record_recent_outcomes_as_habits(self) -> None:
        """Feed recent successful task outcomes into the habit DB (write side of the loop)."""
        store = getattr(self.kernel, "event_store", None) if self.kernel else None
        if store is None or self.learning is None:
            return
        try:
            events = await store.get_recent_events("task_outcome", limit=20)
        except Exception as e:
            logger.debug("event_store read failed: %s", e)
            return
        fresh = [ev for ev in events if ev.id > self._last_outcome_event_id]
        if not fresh:
            return
        self._last_outcome_event_id = max(ev.id for ev in fresh)
        for ev in fresh:
            payload = ev.payload if isinstance(ev.payload, dict) else {}
            if payload.get("ok") and ev.agent_name:
                # TODO: ReflexionEngine will handle this
                pass

    # ── Decision layer ────────────────────────────────────────────────────────

    async def _decide(self, signal: str) -> Optional[dict]:
        if not self.ai or not hasattr(self.ai, "generate"):
            logger.debug("Proactive decision skipped — no ai_handler")
            return None
        try:
            agent_names = sorted(getattr(self.kernel, "agents", {}).keys()) if self.kernel else []
            if not agent_names:
                return None
            recent = await self._recent_task_summary()
            prompt = _DECISION_PROMPT.format(
                signal=str(signal)[:200],
                recent=recent or "(none recorded yet)",
                agents=", ".join(agent_names),
                now=time.strftime("%A %H:%M"),
            )
            resp = await asyncio.wait_for(
                self.ai.generate(
                    messages=[{"role": "user", "content": prompt}],
                    task="intent_classification",
                    require_json=True,
                    temperature=0.2,
                    max_tokens=160,
                ),
                timeout=10.0,
            )
            text = getattr(resp, "text", "") or ""
            parsed = None
            if hasattr(self.ai, "try_parse_json"):
                parsed = self.ai.try_parse_json(text)
            if not isinstance(parsed, dict):
                m = re.search(r"\{[\s\S]*\}", text)
                if m:
                    parsed = json.loads(m.group(0))
            return parsed if isinstance(parsed, dict) else None
        except asyncio.TimeoutError:
            logger.debug("Proactive decision timed out")
        except Exception as e:
            logger.error("Proactive decision failed: %s", e)
        return None

    async def _recent_task_summary(self, limit: int = 8) -> str:
        store = getattr(self.kernel, "event_store", None) if self.kernel else None
        if store is None:
            return ""
        try:
            events = await store.get_recent_events("task_outcome", limit=limit)
            parts = []
            for ev in reversed(events):  # oldest → newest
                ok = bool((ev.payload or {}).get("ok"))
                preview = str((ev.payload or {}).get("goal") or (ev.payload or {}).get("result_preview") or "")[:80]
                if preview:
                    parts.append(f"{ev.agent_name}:{'ok' if ok else 'fail'} ({preview})")
            return "; ".join(parts)
        except Exception as e:
            logger.debug("recent task summary failed: %s", e)
            return ""

    # ── Risk classification ───────────────────────────────────────────────────

    def classify_risk(self, text: str, llm_hint: str = "") -> str:
        """Deterministic keyword classifier; most conservative of (pattern-match, LLM hint) wins."""
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(text):
                return "dangerous"
        if llm_hint in _NOTIFY_HINT:
            return "notify"
        if llm_hint in _SAFE_HINT:
            return "safe"
        if llm_hint == "dangerous":
            return "dangerous"
        return "safe"  # unknown hint → treat as safe-but-audited (reversibility assumed by decision rules)

    # ── Guardrails ────────────────────────────────────────────────────────────

    def _in_quiet_hours(self) -> bool:
        hour = time.localtime().tm_hour
        qs = int(self.cfg.get("quiet_start_hour", 23))
        qe = int(self.cfg.get("quiet_end_hour", 7))
        if qs == qe:
            return False
        return hour >= qs or hour < qe if qs > qe else qs <= hour < qe

    def _rate_limit_ok(self) -> bool:
        now = time.time()
        while self._action_times and now - self._action_times[0] > 86400:
            self._action_times.popleft()
        per_hour = sum(1 for t in self._action_times if now - t < 3600)
        per_day = len(self._action_times)
        return per_hour < int(self.cfg.get("max_actions_per_hour", 3)) and \
            per_day < int(self.cfg.get("max_actions_per_day", 20))

    def _dedup_allow(self, key: str) -> bool:
        now = time.time()
        window = float(self.cfg.get("dedup_window_s", 3600))
        last = self._dedup.get(key, 0.0)
        if now - last < window:
            return False
        # opportunistic pruning
        self._dedup = {k: t for k, t in self._dedup.items() if now - t < window * 4}
        self._dedup[key] = now
        return True

    # ── Execution ─────────────────────────────────────────────────────────────

    async def _execute_proactive(self, agent: str, description: str,
                                 rationale: str, confirmed: bool = False) -> None:
        has_engine = bool(self.orchestration_engine and hasattr(self.orchestration_engine, "run_autonomous_goal"))
        has_kernel = bool(self.kernel and hasattr(self.kernel, "dispatch"))
        if not has_engine and not has_kernel:
            logger.warning("Proactive action skipped — execution engine unavailable")
            return
        if not self._rate_limit_ok():
            logger.info("Proactive action rate-limited — skipping: %s", description[:80])
            await self._audit("rate_limited", agent=agent, description=description,
                              rationale=rationale, tier="safe" if not confirmed else "dangerous")
            return

        task_id = f"pro_{uuid.uuid4().hex[:12]}"
        ok = False
        summary = ""
        try:
            if self.orchestration_engine and hasattr(self.orchestration_engine, "run_autonomous_goal"):
                await self.orchestration_engine.run_autonomous_goal(
                    task_id=task_id,
                    goal=description,
                    context={"source": "proactive_orchestrator", "proactive": True, "agent": agent},
                )
                ok = True
                summary = f"Autonomous goal triggered via OpenAI Agents SDK: {description}"
            elif self.kernel and hasattr(self.kernel, "dispatch"):
                result = await self.kernel.dispatch(
                    task_id=task_id,
                    agent_name=agent,
                    message=description,
                    context={"source": "proactive_orchestrator", "proactive": True},
                    entities={},
                    is_background=True,
                )
                ok = bool(result and getattr(result, "success", False))
                summary = str(getattr(result, "result", "") or getattr(result, "error", "") or "")[:300]
            else:
                logger.warning("[ProactiveOrchestrator] No execution engine available to dispatch task")
        except Exception as e:
            logger.error("Proactive dispatch failed (%s): %s", agent, e)

        self._action_times.append(time.time())
        status = "confirmed_executed" if confirmed else "executed"
        await self._audit(status if ok else "failed", agent=agent, description=description,
                          rationale=rationale, tier="safe" if not confirmed else "dangerous",
                          task_id=task_id, result_preview=summary)
        await self._emit_ws("proactive_action", {
            "status": status if ok else "failed",
            "agent": agent,
            "description": description,
            "rationale": rationale,
            "task_id": task_id,
            "result_preview": summary,
        })

    async def _request_confirmation(self, description: str, rationale: str) -> bool:
        """Raise the standard ACTION_CONFIRM_REQUEST card and wait on BaseAgent's resolver."""
        try:
            from .agents.base_agent import BaseAgent
            from . import ws_protocol
        except Exception as e:
            logger.error("Confirmation infra unavailable: %s", e)
            return False
        if not self.ws_broadcast:
            return False

        confirm_action = "proactive_action"
        task_id = f"pro_{uuid.uuid4().hex[:12]}"
        confirm_id = f"{task_id}_{confirm_action}"
        event = asyncio.Event()
        res = {"approved": False}
        BaseAgent._pending_confirmations[confirm_id] = (event, confirm_action, res)
        BaseAgent._pending_confirmations[task_id] = (event, confirm_action, res)
        try:
            await self.ws_broadcast(ws_protocol.build_action_confirm(
                task_id=task_id, action=confirm_action,
                description=f"[Proactive] {description}\nWhy: {rationale}",
                risk_level="high",
            ))
            timeout = float(self.cfg.get("confirm_timeout_s", 90))
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return bool(res["approved"])
        except asyncio.TimeoutError:
            logger.info("Proactive confirmation timed out for: %s", description[:80])
            return False
        finally:
            BaseAgent._pending_confirmations.pop(confirm_id, None)
            BaseAgent._pending_confirmations.pop(task_id, None)

    # ── Emission & audit ──────────────────────────────────────────────────────

    async def _broadcast_proposal(self, agent: str, description: str,
                                  rationale: str, tier: str) -> None:
        await self._emit_ws("proactive_suggestion", {
            "agent": agent, "description": description,
            "rationale": rationale, "tier": tier,
        })
        await self._audit("suggested", agent=agent, description=description,
                          rationale=rationale, tier=tier)

    async def _audit(self, status: str, *, agent: str, description: str,
                     rationale: str, tier: str, task_id: str = "",
                     result_preview: str = "") -> None:
        store = getattr(self.kernel, "event_store", None) if self.kernel else None
        if store is None:
            return
        try:
            await store.append(
                "proactive_action", task_id=task_id or None, agent_name=agent,
                payload={"status": status, "tier": tier, "description": description[:300],
                         "rationale": rationale, "result_preview": result_preview[:200]},
            )
        except TypeError:
            # EventStore.append may not accept task_id=None positionally in older builds
            try:
                await store.append("proactive_action", agent_name=agent,
                                   payload={"status": status, "tier": tier,
                                            "description": description[:300],
                                            "rationale": rationale})
            except Exception as e:
                logger.debug("Proactive audit (fallback) failed: %s", e)
        except Exception as e:
            logger.debug("Proactive audit failed: %s", e)

    async def _emit_ws(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.ws_broadcast:
            return
        try:
            from . import ws_protocol
            await self.ws_broadcast(ws_protocol.build_proactive_event(event_type, payload))
        except Exception as e:
            logger.debug("Proactive WS emit failed (%s): %s", event_type, e)
