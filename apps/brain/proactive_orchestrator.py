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
from enum import IntEnum
from pathlib import Path
from typing import Any

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

DEFAULT_CONFIG: dict[str, Any] = {
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
        # Outbound communication to real people (exclude postpone)
        r"\b(send|reply|forward|tweet|publish|email)\w*\b|\bpost(s|ed|ing)?\b",
        # Money & credentials (exclude 'in order to')
        r"\b(pay|payment|purchase|buy|checkout|transfer)\w*\b|(?<!in\s)\border\b(?!\s+to\b)",
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


class ProactiveOrchestrator:
    """Signal-driven proactive action engine with risk-tiered autonomy."""

    def __init__(
        self,
        config: dict | None = None,
        ws_broadcast: Any = None,
        ai_handler: Any = None,
        learning_engine: Any = None,
        kernel: Any = None,
        orchestration_engine: Any = None,
        learning: Any = None,
        **kwargs: Any,
    ) -> None:
        raw_cfg: dict[str, Any] = {}
        if isinstance(config, dict):
            p_cfg = config.get("proactive")
            if isinstance(p_cfg, dict):
                raw_cfg = p_cfg
        self.cfg: dict[str, Any] = {**DEFAULT_CONFIG, **{k: v for k, v in raw_cfg.items() if v is not None}}
        self.ws_broadcast = ws_broadcast
        self.ai = ai_handler
        # TODO: ReflexionEngine will handle this
        self.learning = None
        self.kernel = kernel
        self.orchestration_engine = orchestration_engine
        self.durable_task_engine = kwargs.get("durable_task_engine") or getattr(kernel, "durable_task_engine", None)
        self.event_store = (
            kwargs.get("event_store")
            or getattr(self.kernel, "event_store", None)
            or getattr(self.durable_task_engine, "_event_store", None)
        )

        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._durable_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._proactive_enabled_by_profile: bool = True
        self.mode: str = str(self.cfg.get("mode", "suggest"))
        self._load_persisted_mode()

        # Guardrail state
        self._action_times: deque[float] = deque()          # executed action timestamps
        self._dedup: dict[str, float] = {}                  # proposal_hash -> last ts
        self._last_outcome_event_id: int = 0                 # habit-loop cursor

        # Best-effort state file next to voice_config.json conventions
        self._state_path = Path(__file__).resolve().parents[2] / "configs" / "proactive_state.json"

    def _cfg_int(self, key: str, default: int) -> int:
        val = self.cfg.get(key)
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def _cfg_float(self, key: str, default: float) -> float:
        val = self.cfg.get(key)
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self.cfg.get("enabled", True):
            logger.info("ProactiveOrchestrator disabled by config — skipping start")
            return
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._loop(), name="proactive-loop")
        self._durable_task = asyncio.create_task(self._durable_loop(), name="proactive-durable-loop")
        logger.info(
            "ProactiveOrchestrator started (mode=%s, interval=%ss, max/h=%.0f)",
            self.mode, self._cfg_float("interval_s", 300.0), float(self._cfg_int("max_actions_per_hour", 3)),
        )

    async def stop(self) -> None:
        self._running = False
        tasks_to_cancel: list[asyncio.Task] = []
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            tasks_to_cancel.append(self._loop_task)
        if self._durable_task and not self._durable_task.done():
            self._durable_task.cancel()
            tasks_to_cancel.append(self._durable_task)
        for t in list(self._background_tasks):
            if not t.done():
                t.cancel()
                tasks_to_cancel.append(t)
        self._background_tasks.clear()
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        logger.info("ProactiveOrchestrator stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def set_focus_profile(
        self,
        profile: str | dict[str, Any],
        toggles: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply focus profile constraints to proactive orchestrator.

        Supports:
        - set_focus_profile("work", toggles_dict)  # called by main.py
        - set_focus_profile("quiet")               # profile string only
        - set_focus_profile({"profile": "quiet", "proactive_suggestions_enabled": False}) # dict only
        """
        active_toggles: dict[str, Any] = {}
        if isinstance(profile, dict):
            profile_name = str(profile.get("profile") or profile.get("name") or "custom").strip().lower()
            active_toggles = dict(profile)
            if toggles and isinstance(toggles, dict):
                active_toggles.update(toggles)
        elif isinstance(profile, str):
            profile_name = profile.strip().lower()
            if toggles and isinstance(toggles, dict):
                active_toggles = dict(toggles)
            elif profile_name in ("quiet", "meeting", "gaming", "deep_work", "deep-work", "focus"):
                active_toggles["proactive_suggestions_enabled"] = False
            elif profile_name in ("work", "normal", "default"):
                active_toggles["proactive_suggestions_enabled"] = True
        else:
            profile_name = "unknown"
            if toggles and isinstance(toggles, dict):
                active_toggles = dict(toggles)

        for key in ("proactive_suggestions_enabled", "suggestions_enabled"):
            if key in active_toggles:
                enabled = bool(active_toggles[key])
                self.cfg["enabled"] = enabled
                self._proactive_enabled_by_profile = enabled
                break

        self._active_focus_profile = profile_name
        logger.info(
            "[ProactiveOrchestrator] Applied focus profile: %s (enabled=%s)",
            profile_name,
            self.cfg.get("enabled", True),
        )
        return {"ok": True, "profile": profile_name, "enabled": self.cfg.get("enabled", True)}

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
            "focus_profile": getattr(self, "_active_focus_profile", "work"),
            "focus_proactive_enabled": getattr(self, "_proactive_enabled_by_profile", True),
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

    async def _durable_loop(self) -> None:
        """Fast 2-3s poll loop dedicated to waking up scheduled durable tasks and reminders."""
        while self._running:
            try:
                await self._check_durable_task_resumptions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("[ProactiveOrchestrator] Durable task resumption check error: %s", e)
            await asyncio.sleep(2.5)

    async def _loop(self) -> None:
        interval = max(30.0, self._cfg_float("interval_s", 300.0))
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Proactive tick failed: %s", e)
            await asyncio.sleep(interval)

    async def _tick(self) -> None:
        # Check for scheduled durable task resumptions (Module 4) - ALWAYS run regardless of mode/quiet hours
        await self._check_durable_task_resumptions()

        if self.mode == "off":
            return

        if not getattr(self, "_proactive_enabled_by_profile", True):
            return

        if self._in_quiet_hours():
            return

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
                    if resumed and self.orchestration_engine and hasattr(self.orchestration_engine, "run_autonomous_goal"):
                        # Re-arm repeats / disarm one-shots so a fired reminder
                        # never refires on the next tick.
                        rctx = resumed.reconstructed_context if isinstance(getattr(resumed, "reconstructed_context", None), dict) else {}
                        try:
                            rep = rctx.get("repeat_interval_s")
                            if rep and float(rep) >= 60:
                                await dte.checkpoint_task(
                                    task_id=cp.task_id,
                                    prompt=cp.original_prompt,
                                    task_name=cp.task_name,
                                    context=dict(rctx),
                                    resume_after_seconds=float(rep),
                                    status="active",
                                    original_prompt=cp.original_prompt,
                                )
                            else:
                                await dte.mark_completed(cp.task_id, final_result="reminder fired")
                        except Exception as rearm_err:
                            logger.debug("[ProactiveOrchestrator] Reminder re-arm error: %s", rearm_err)

                        task_coro = self.orchestration_engine.run_autonomous_goal(
                            task_id=cp.task_id,
                            goal=resumed.continuation_prompt,
                            conversation_id=rctx.get("conversation_id", "default_session"),
                            context=rctx,
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
            from .core.os_state import get_os_state
            bat = get_os_state().get_battery_status()
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
            from .core.os_state import get_os_state
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
        autonomy = self._cfg_int("autonomy_level", int(AutonomyLevel.LEVEL_2_SUGGESTION_CARD))
        tier = self.classify_risk(task_desc, llm_hint=risk_hint)

        if autonomy == AutonomyLevel.LEVEL_0_OBSERVATIONAL:
            await self._audit("observed_only", agent=agent, description=task_desc, rationale=rationale, tier=tier)
            return

        if self.mode == "suggest" or autonomy <= AutonomyLevel.LEVEL_2_SUGGESTION_CARD or tier == "notify":
            await self._broadcast_proposal(agent, task_desc, rationale, tier)
            return

        if tier == "safe" and autonomy >= AutonomyLevel.LEVEL_3_REVERSIBLE_AUTO:
            await self._execute_proactive(agent, task_desc, rationale)
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
        store = (
            self.event_store
            or (getattr(self.kernel, "event_store", None) if self.kernel else None)
            or (getattr(self.durable_task_engine, "_event_store", None) if self.durable_task_engine else None)
        )
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

    # ── Risk classification ───────────────────────────────────────────────────

    def classify_risk(self, text: str, llm_hint: str = "") -> str:
        """Deterministic keyword classifier; most conservative of (pattern-match, LLM hint) wins."""
        clean_text = text or ""
        clean_text = re.sub(r"\bin order to\b", "", clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r"\bpostpone\w*\b", "", clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r"\blooking forward to\b", "", clean_text, flags=re.IGNORECASE)

        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(clean_text):
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
        qs = self._cfg_int("quiet_start_hour", 23)
        qe = self._cfg_int("quiet_end_hour", 7)
        if qs == qe:
            return False
        return hour >= qs or hour < qe if qs > qe else qs <= hour < qe

    def _rate_limit_ok(self) -> bool:
        now = time.time()
        while self._action_times and now - self._action_times[0] > 86400:
            self._action_times.popleft()
        per_hour = sum(1 for t in self._action_times if now - t < 3600)
        per_day = len(self._action_times)
        return per_hour < self._cfg_int("max_actions_per_hour", 3) and \
            per_day < self._cfg_int("max_actions_per_day", 20)

    def _dedup_allow(self, key: str) -> bool:
        now = time.time()
        window = self._cfg_float("dedup_window_s", 3600.0)
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
            from . import ws_protocol
            from .core.confirmations import ActionConfirmationManager as BaseAgent
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
            timeout = self._cfg_float("confirm_timeout_s", 90.0)
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
        store = (
            self.event_store
            or (getattr(self.kernel, "event_store", None) if self.kernel else None)
            or (getattr(self.durable_task_engine, "_event_store", None) if self.durable_task_engine else None)
        )
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

    async def _emit_ws(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.ws_broadcast:
            return
        try:
            from . import ws_protocol
            await self.ws_broadcast(ws_protocol.build_proactive_event(event_type, payload))
        except Exception as e:
            logger.debug("Proactive WS emit failed (%s): %s", event_type, e)
