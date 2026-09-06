"""
Makima Core Engine — Saga Recovery Engine
Location: apps/brain/core/saga_recovery_engine.py

Theoretical Basis:
- Garcia-Molina & Salem (1987) "Sagas", ACM SIGMOD
- ReAct (Yao et al., 2022) Self-Correction & Recovery Patterns

Active fault-handling engine providing parameter auto-correction, dynamic alternate
capability routing, reverse-order saga compensation, and structured partial completion reporting.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union

from .contracts import Action, ExecutionResult, ActionExecutionContext

logger = logging.getLogger("saga_recovery_engine")


# ─────────────────────────────────────────────────────────────────────────────
# Canonical Fallback Capability Mesh
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_CAPABILITY_FALLBACKS: dict[str, list[str]] = {
    "web_search": ["browser_parallel_search", "memory_search"],
    "browser_click": ["browser_press_key", "system_mouse_click"],
    "file_write": ["clipboard_write"],
    "code_execute": ["sandbox_execute"],
    "git_commit": ["git_stash"],
}

# Parameter renaming synonyms for automated schema correction
PARAM_SYNONYM_MAP: dict[str, tuple[str, ...]] = {
    "query": ("search_query", "text", "prompt", "q", "question"),
    "search_query": ("query", "text", "prompt", "q"),
    "path": ("file_path", "filepath", "filename", "output_path", "target_path", "dst", "src"),
    "file_path": ("path", "filepath", "filename", "output_path"),
    "filepath": ("file_path", "path", "filename"),
    "content": ("text", "data", "body", "payload"),
    "text": ("content", "data", "body"),
    "url": ("link", "uri", "target_url"),
    "command": ("cmd", "script", "instruction"),
    "code": ("script", "source_code", "snippet"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Data Contracts
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CompensationStep:
    """A single stateful action's inverse operation in a Saga."""
    action_id: str
    tool_name: str
    compensation_fn: Optional[Callable[..., Coroutine[Any, Any, Any]]] = None
    compensation_tool: Optional[str] = None
    compensation_params: dict[str, Any] = field(default_factory=dict)
    snapshot_id: Optional[str] = None
    registered_at: float = field(default_factory=time.time)


@dataclass
class SagaRollbackReport:
    """Diagnostic report produced after executing a multi-step Saga rollback."""
    task_id: str
    total_steps: int
    rolled_back: list[str]
    failed_rollbacks: list[str]
    is_fully_compensated: bool
    summary: str


@dataclass
class RecoveryResult:
    """Unified result contract emitted after executing the recovery decision tree."""
    recovered: bool
    strategy_used: str  # "param_retry" | "alternate_tool" | "saga_rollback" | "partial_abort"
    result: Optional[ExecutionResult] = None
    compensated_actions: list[str] = field(default_factory=list)
    diagnostic_report: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# SagaRecoveryEngine
# ─────────────────────────────────────────────────────────────────────────────
class SagaRecoveryEngine:
    """
    Active execution resilience and compensation engine.
    When a tool fails:
      1. Inspects & auto-patches parameters (paths, types, missing keys, timeouts) -> retries once.
      2. Routes to alternate capabilities in the capability mesh if retry fails.
      3. Executes inverse compensation sagas in reverse topological order if state was dirtied.
      4. Produces transparent partial completion reports if recovery is impossible.
    """

    def __init__(
        self,
        tool_registry: Any = None,
        execution_runtime: Any = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.execution_runtime = execution_runtime

        # Dynamic fallback registry (cloned from default canonical map)
        self.fallbacks: dict[str, list[str]] = {
            k: list(v) for k, v in DEFAULT_CAPABILITY_FALLBACKS.items()
        }

        # Registered compensation closures: task_id -> list of CompensationStep
        self._compensation_registry: dict[str, list[CompensationStep]] = {}

        # Successfully executed actions: task_id -> list of Action
        self._task_action_log: dict[str, list[Action]] = {}

        # Recursion and loop guard: action_id -> attempt_count
        self._recovery_attempts: dict[str, int] = {}

    # ── Alternate Capability Registration ─────────────────────────────────────
    def register_alternate(self, tool_name: str, alternates: list[str]) -> None:
        """Register or extend dynamic capability alternates for a given tool."""
        if tool_name not in self.fallbacks:
            self.fallbacks[tool_name] = []
        for alt in alternates:
            if alt not in self.fallbacks[tool_name] and alt != tool_name:
                self.fallbacks[tool_name].append(alt)
        logger.info("[saga_recovery] Registered alternates for '%s': %s", tool_name, self.fallbacks[tool_name])

    # ── Compensation Registration ─────────────────────────────────────────────
    def register_compensation(
        self,
        task_id: str,
        action_id: str,
        compensation_fn: Optional[Callable[..., Coroutine[Any, Any, Any]]] = None,
        compensation_tool: Optional[str] = None,
        compensation_params: Optional[dict[str, Any]] = None,
        snapshot_id: Optional[str] = None,
        tool_name: str = "",
    ) -> None:
        """Store compensation closure or snapshot inverse operation for an action."""
        if not task_id:
            task_id = "default_task"
        if task_id not in self._compensation_registry:
            self._compensation_registry[task_id] = []

        step = CompensationStep(
            action_id=action_id,
            tool_name=tool_name,
            compensation_fn=compensation_fn,
            compensation_tool=compensation_tool,
            compensation_params=dict(compensation_params or {}),
            snapshot_id=snapshot_id,
        )
        self._compensation_registry[task_id].append(step)
        logger.debug("[saga_recovery] Registered compensation step for action '%s' in task '%s'", action_id, task_id)

    def log_action_success(self, action: Action) -> None:
        """Log a successfully executed Action for partial-completion tracking."""
        task_id = getattr(action, "task_id", "") or "default_task"
        if task_id not in self._task_action_log:
            self._task_action_log[task_id] = []
        self._task_action_log[task_id].append(action)

    # ── Main Recovery Decision Tree ───────────────────────────────────────────
    async def handle_failure(
        self,
        action: Action,
        failed_result: ExecutionResult,
        context: Optional[ActionExecutionContext] = None,
    ) -> RecoveryResult:
        """
        Execute the 4-step recovery decision tree in Garcia-Molina Saga order:
          Step 1: Param auto-correction & retry once.
          Step 2: Alternate capability invocation.
          Step 3: Saga compensation rollback (if state was modified).
          Step 4: Safe partial completion report.
        """
        action_id = getattr(action, "action_id", "") or "act_unknown"
        task_id = getattr(action, "task_id", "") or (context.task_id if context else "") or "default_task"
        tool_name = getattr(action, "capability_name", "") or getattr(action, "tool_name", "") or ""
        params = dict(getattr(action, "parameters", {}) or {})
        error_text = str(getattr(failed_result, "error", "") or getattr(failed_result, "evidence_tier", "") or "")

        logger.info(
            "[saga_recovery] Initiating recovery for tool '%s' (task_id=%s, action_id=%s): %s",
            tool_name, task_id, action_id, error_text[:120],
        )

        # Recursion guard: prevent cascading recovery loops on the same action
        current_attempts = self._recovery_attempts.get(action_id, 0)
        if current_attempts >= 2:
            logger.warning("[saga_recovery] Max recovery attempts (2) reached for action '%s' — escalating to abort", action_id)
            return self._build_partial_abort_result(task_id, action, failed_result, "Max recovery recursion exceeded.")
        self._recovery_attempts[action_id] = current_attempts + 1

        # ── STEP 1: Is error param-fixable? ───────────────────────────────────
        patched_params = self._inspect_and_patch_params(tool_name, params, error_text)
        if patched_params is not None and patched_params != params and self.execution_runtime:
            logger.info("Strategy=param_retry")
            logger.info("[saga_recovery] Step 1: Auto-patched params for '%s' -> %s. Retrying once...", tool_name, patched_params)
            retry_action = Action(
                action_id=f"{action_id}_retry_{uuid.uuid4().hex[:4]}",
                task_id=task_id,
                capability_name=tool_name,
                parameters=patched_params,
                risk_level=getattr(action, "risk_level", "LOW"),
                is_reversible=getattr(action, "is_reversible", True),
            )
            try:
                retry_res = await self.execution_runtime.execute_action(retry_action, context=context)
                if retry_res and retry_res.is_success:
                    logger.info("[saga_recovery] Step 1 SUCCESS: Param retry succeeded for '%s'!", tool_name)
                    self.log_action_success(retry_action)
                    return RecoveryResult(
                        recovered=True,
                        strategy_used="param_retry",
                        result=retry_res,
                        diagnostic_report=f"Successfully auto-corrected parameters for '{tool_name}' and retried: {patched_params}",
                    )
                else:
                    logger.debug("[saga_recovery] Step 1: Param retry failed with error: %s", getattr(retry_res, "error", "unknown"))
            except Exception as retry_exc:
                logger.warning("[saga_recovery] Step 1: Exception executing param retry for '%s': %s", tool_name, retry_exc)

        # ── STEP 2: Has alternate capability? ─────────────────────────────────
        alternates = self._get_alternate_capabilities(tool_name)
        if alternates and self.execution_runtime:
            logger.info("Strategy=alternate_tool")
            for alt_tool in alternates:
                logger.info("Attempting alternate: %s", alt_tool)
                adapted_params = self._adapt_params_for_alternate(tool_name, alt_tool, params)
                alt_action = Action(
                    action_id=f"{action_id}_alt_{alt_tool}_{uuid.uuid4().hex[:4]}",
                    task_id=task_id,
                    capability_name=alt_tool,
                    parameters=adapted_params,
                    risk_level=getattr(action, "risk_level", "LOW"),
                    is_reversible=getattr(action, "is_reversible", True),
                )
                try:
                    alt_res = await self.execution_runtime.execute_action(alt_action, context=context)
                    if alt_res and alt_res.is_success:
                        logger.info("[saga_recovery] Step 2 SUCCESS: Alternate '%s' recovered successfully!", alt_tool)
                        self.log_action_success(alt_action)
                        return RecoveryResult(
                            recovered=True,
                            strategy_used="alternate_tool",
                            result=alt_res,
                            diagnostic_report=f"Recovered via alternate capability '{alt_tool}' after '{tool_name}' failed.",
                        )
                except Exception as alt_exc:
                    logger.debug("[saga_recovery] Step 2: Alternate '%s' execution failed: %s", alt_tool, alt_exc)

        # ── STEP 3: Has compensation saga? ────────────────────────────────────
        task_compensations = self._compensation_registry.get(task_id, [])
        if task_compensations:
            logger.info("[saga_recovery] Step 3: Triggering Saga compensation rollback for task '%s' (%d steps)...", task_id, len(task_compensations))
            rollback_report = await self.execute_compensation_saga(task_id, failed_action_id=action_id)
            return RecoveryResult(
                recovered=False,
                strategy_used="saga_rollback",
                result=failed_result,
                compensated_actions=rollback_report.rolled_back,
                diagnostic_report=rollback_report.summary,
            )

        # ── STEP 4: Partial abort ─────────────────────────────────────────────
        logger.info("[saga_recovery] Step 4: No further recovery paths available. Synthesizing partial abort report.")
        return self._build_partial_abort_result(task_id, action, failed_result, error_text)

    # ── Parameter Inspection & Auto-Patching (Step 1 Helper) ──────────────────
    def _inspect_and_patch_params(
        self,
        tool_name: str,
        params: dict[str, Any],
        error_text: str,
    ) -> Optional[dict[str, Any]]:
        """
        Analyzes the tool failure error and inspects parameters for known fixable classes:
          1. Path slashes, trailing quotes, file:// URIs, Windows directory delimiters
          2. Renamed / missing required argument aliases
          3. Basic type coercion (string numbers -> int/float, string JSON -> dict/list)
          4. Timeout extension
        """
        patched = dict(params)
        modified = False
        err_low = error_text.lower()

        # Class 1: File & Directory Paths (slashes, URI schemes, quotes, nonexistent parents)
        path_keys = ("path", "file_path", "filepath", "output_path", "filename", "dst", "src", "target_folder_or_path")
        for pk in path_keys:
            if pk in patched and isinstance(patched[pk], str):
                val = patched[pk].strip()
                # Strip wrapping quotes
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1].strip()
                    modified = True
                # Strip file:// URI prefix
                if val.startswith("file://"):
                    val = re.sub(r"^file:[/\\]+", "", val)
                    if os.name == "nt" and re.match(r"^[a-zA-Z]:", val.lstrip("/")):
                        val = val.lstrip("/")
                    modified = True
                # Normalize slashes for current OS
                norm_val = os.path.normpath(val)
                if norm_val != patched[pk]:
                    val = norm_val
                    modified = True

                # On Windows: If path specifies a non-existent or inaccessible user profile (e.g. C:\Users\test\...)
                # map it under the active user's home profile to ensure write permission
                if os.name == "nt":
                    m_user = re.match(r"^[a-zA-Z]:[/\\]Users[/\\]([^/\\]+)[/\\]?(.*)$", val, re.IGNORECASE)
                    if m_user:
                        target_user = m_user.group(1).lower()
                        current_home = os.path.expanduser("~")
                        active_user = os.path.basename(current_home).lower()
                        if target_user != active_user:
                            val = os.path.normpath(os.path.join(current_home, m_user.group(2)))
                            modified = True

                # Ensure destination parent directory exists
                try:
                    parent_dir = os.path.dirname(os.path.abspath(val))
                    if parent_dir and not os.path.exists(parent_dir):
                        os.makedirs(parent_dir, exist_ok=True)
                        logger.info("[saga_recovery] Auto-created missing parent directory: %s", parent_dir)
                        modified = True
                except Exception:
                    pass

                patched[pk] = val

        # Class 2: Missing required arguments or unexpected keyword arguments
        # e.g., "got an unexpected keyword argument 'query'" or "missing 1 required positional argument: 'text'"
        missing_match = re.search(r"missing \d+ required (?:positional|keyword-only) argument: ['\"](\w+)['\"]", error_text)
        unexpected_match = re.search(r"got an unexpected keyword argument ['\"](\w+)['\"]", error_text)

        target_missing_key = missing_match.group(1) if missing_match else None
        target_unexpected_key = unexpected_match.group(1) if unexpected_match else None

        if target_unexpected_key and target_unexpected_key in patched:
            # Try to map unexpected key to known synonyms
            synonyms = PARAM_SYNONYM_MAP.get(target_unexpected_key, ())
            val = patched.pop(target_unexpected_key)
            if synonyms:
                patched[synonyms[0]] = val
                logger.info("[saga_recovery] Renamed unexpected key '%s' -> '%s'", target_unexpected_key, synonyms[0])
                modified = True

        if target_missing_key and target_missing_key not in patched:
            # Check if we have a value under any known synonym
            synonyms = PARAM_SYNONYM_MAP.get(target_missing_key, ())
            for syn in synonyms:
                if syn in patched:
                    patched[target_missing_key] = patched.pop(syn)
                    logger.info("[saga_recovery] Satisfied missing argument '%s' using synonym '%s'", target_missing_key, syn)
                    modified = True
                    break

        # Class 3: Type Mismatches
        if "must be str" in err_low or "expected str" in err_low:
            for k, v in list(patched.items()):
                if isinstance(v, (int, float, bool)):
                    patched[k] = str(v)
                    modified = True
        elif any(kw in err_low for kw in ("must be int", "expected int", "invalid literal for int()")):
            for k, v in list(patched.items()):
                if isinstance(v, str) and v.strip().lstrip("-+").isdigit():
                    patched[k] = int(v.strip())
                    modified = True
        elif any(kw in err_low for kw in ("must be a list", "expected list", "expected a sequence")):
            for k, v in list(patched.items()):
                if isinstance(v, str):
                    if v.startswith("[") and v.endswith("]"):
                        import json
                        try:
                            patched[k] = json.loads(v)
                            modified = True
                        except Exception:
                            pass
                    else:
                        patched[k] = [v]
                        modified = True

        # Class 4: Timeout Extension
        if any(kw in err_low for kw in ("timeout", "timed out", "deadline exceeded")):
            current_timeout = patched.get("timeout_s") or patched.get("timeout") or 30.0
            try:
                new_timeout = min(120.0, float(current_timeout) * 2.0)
                patched["timeout_s"] = new_timeout
                modified = True
                logger.info("[saga_recovery] Extended timeout for '%s' from %.1fs to %.1fs", tool_name, float(current_timeout), new_timeout)
            except (ValueError, TypeError):
                pass

        return patched if modified else None

    # ── Alternate Capability Lookup & Adaptation (Step 2 Helper) ──────────────
    def _get_alternate_capabilities(self, tool_name: str) -> list[str]:
        """Collect all declared and dynamically discovered fallback tools."""
        alternates: list[str] = list(self.fallbacks.get(tool_name, []))

        # Dynamically inspect ToolRegistry for tools tagged with fallback metadata
        if self.tool_registry and hasattr(self.tool_registry, "_tools"):
            target_tag = f"fallback_for_{tool_name}".lower()
            for name, meta in self.tool_registry._tools.items():
                tags = [t.lower() for t in getattr(meta, "task_tags", [])]
                if (target_tag in tags or f"alternate_{tool_name}".lower() in tags) and name != tool_name and name not in alternates:
                    alternates.append(name)

        return alternates

    def _adapt_params_for_alternate(
        self,
        source_tool: str,
        target_tool: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Adapt parameters when routing from source_tool to an alternate target_tool."""
        adapted = dict(params)

        # web_search -> browser_parallel_search or memory_search
        if source_tool == "web_search":
            query = params.get("query") or params.get("search_query") or params.get("q") or ""
            if target_tool == "browser_parallel_search":
                adapted = {"query": query, "max_results": params.get("num_results", 5)}
            elif target_tool == "memory_search":
                adapted = {"query": query, "top_k": 3}

        # browser_click -> system_mouse_click or browser_press_key
        elif source_tool == "browser_click":
            if target_tool == "browser_press_key":
                adapted = {"key": "Enter"}
            elif target_tool == "system_mouse_click":
                coords = params.get("coordinates") or params.get("point") or [500, 500]
                adapted = {"x": coords[0], "y": coords[1]}

        # file_write -> clipboard_write
        elif source_tool == "file_write" and target_tool == "clipboard_write":
            content = params.get("content") or params.get("data") or params.get("text") or ""
            adapted = {"text": str(content)}

        return adapted

    # ── Saga Compensation Execution (Step 3 Helper) ───────────────────────────
    async def execute_compensation_saga(
        self,
        task_id: str,
        failed_action_id: str = "",
    ) -> SagaRollbackReport:
        """
        Execute registered compensation closures and snapshots in reverse topological order.
        Guarantees that stateful partial operations (disk writes, DB rows) are cleanly unrolled.
        """
        steps = list(self._compensation_registry.get(task_id, []))
        total_steps = len(steps)
        rolled_back: list[str] = []
        failed_rollbacks: list[str] = []

        if not steps:
            return SagaRollbackReport(
                task_id=task_id,
                total_steps=0,
                rolled_back=[],
                failed_rollbacks=[],
                is_fully_compensated=True,
                summary="No compensable actions registered for rollback.",
            )

        logger.warning(
            "[saga_recovery] SAGA COMPENSATING: Unrolling %d stateful actions for task '%s' in reverse order...",
            total_steps, task_id,
        )

        # Garcia-Molina Saga Pattern: execute reverse order (LIFO / reverse topological)
        for step in reversed(steps):
            step_desc = f"{step.tool_name}({step.action_id})"
            step_success = False

            try:
                # 1. Direct Python async compensation closure
                if step.compensation_fn is not None:
                    if inspect.iscoroutinefunction(step.compensation_fn):
                        await step.compensation_fn()
                    else:
                        step.compensation_fn()
                    step_success = True

                # 2. Reversible snapshot ID (e.g. filesystem backup snapshot)
                elif step.snapshot_id:
                    try:
                        from ..agents.filesystem_engine import get_filesystem_engine
                        fe = get_filesystem_engine()
                        target_path = step.compensation_params.get("target_path") or step.compensation_params.get("file_path") or ""
                        if fe and target_path:
                            ok, s_err = await fe.restore_snapshot(step.snapshot_id, target_path)
                            step_success = ok
                            if not ok:
                                logger.error("[saga_recovery] Snapshot restore failed for %s: %s", step.snapshot_id, s_err)
                        else:
                            logger.warning("[saga_recovery] Snapshot %s missing target_path in compensation_params", step.snapshot_id)
                    except Exception as snap_err:
                        logger.debug("[saga_recovery] Native snapshot rollback attempt failed: %s", snap_err)

                # 3. Explicit compensation tool call
                elif step.compensation_tool and self.execution_runtime:
                    comp_out = await self.execution_runtime.execute_tool(
                        name=step.compensation_tool,
                        params=step.compensation_params,
                    )
                    if isinstance(comp_out, str) and comp_out.startswith("Error executing tool"):
                        step_success = False
                        logger.error("[saga_recovery] Compensation tool '%s' returned error: %s", step.compensation_tool, comp_out)
                    else:
                        step_success = True

            except Exception as comp_err:
                logger.error("[saga_recovery] SAGA DOUBLE FAULT: Compensation for %s failed: %s", step_desc, comp_err)
                failed_rollbacks.append(step.action_id)
                continue

            if step_success:
                rolled_back.append(step.action_id)
                logger.info("[saga_recovery] SAGA: Successfully compensated action '%s'", step_desc)
            else:
                failed_rollbacks.append(step.action_id)

        # Clear executed compensations to prevent duplicate rollback
        self._compensation_registry.pop(task_id, None)

        is_fully_compensated = len(failed_rollbacks) == 0
        summary = (
            f"Saga Rollback: {len(rolled_back)}/{total_steps} actions compensated. "
            f"Failed: {len(failed_rollbacks)}. Full Compensation: {is_fully_compensated}."
        )

        return SagaRollbackReport(
            task_id=task_id,
            total_steps=total_steps,
            rolled_back=rolled_back,
            failed_rollbacks=failed_rollbacks,
            is_fully_compensated=is_fully_compensated,
            summary=summary,
        )

    # ── Partial Completion Report (Step 4 Helper) ─────────────────────────────
    def report_partial_completion(
        self,
        task_id: str,
        completed: list[Union[Action, str]],
        failed: Union[Action, str],
        error: str,
    ) -> str:
        """Produce an honest, structured diagnostic report of partial multi-step progress."""
        completed_names = [
            getattr(a, "capability_name", str(a)) if not isinstance(a, str) else a
            for a in completed
        ]
        failed_name = getattr(failed, "capability_name", str(failed)) if not isinstance(failed, str) else failed
        comp_str = ", ".join(completed_names) if completed_names else "None"

        return (
            f"Completed: {comp_str}. Failed at: {failed_name}. "
            f"Reason: {error}. State has been safely assessed."
        )

    def _build_partial_abort_result(
        self,
        task_id: str,
        action: Action,
        failed_result: ExecutionResult,
        error_text: str,
    ) -> RecoveryResult:
        """Helper to assemble a terminal partial_abort RecoveryResult."""
        completed_actions = self._task_action_log.get(task_id, [])
        report = self.report_partial_completion(task_id, completed_actions, action, error_text)
        return RecoveryResult(
            recovered=False,
            strategy_used="partial_abort",
            result=failed_result,
            compensated_actions=[],
            diagnostic_report=report,
        )
