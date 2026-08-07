"""
Makima v7.2 — Elite Peer-to-Peer Consultation Protocol

Allows any agent to consult a peer mid-execution without going through the
orchestrator's full dispatch cycle. Enables emergent collaboration patterns
like code_agent asking security_agent "is this snippet safe?" or
research_agent asking data_analyst "can you parse this table?".

Features:
  - Async request/response with configurable timeout
  - Budget guardrails: max consultations per task (prevents runaway chains)
  - Anti-recursion: prevents A→B→A infinite consult loops via chain tracking
  - Consultation types: review, verify, advise, delegate_partial
  - Lightweight: uses LLM call directly (no orchestrator dispatch overhead)
  - Result caching for identical consultations within a task
  - Full audit trail of all consultations per task
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("makima.coordination.consultation")


class ConsultationType(str, Enum):
    """What kind of help is being requested."""
    REVIEW = "review"          # "Review my output for correctness"
    VERIFY = "verify"          # "Is this safe / correct / valid?"
    ADVISE = "advise"          # "What should I do next?"
    DELEGATE_PARTIAL = "delegate_partial"  # "Handle this sub-part for me"


@dataclass
class ConsultationRequest:
    """A consultation request from one agent to another."""
    from_agent: str
    to_agent: str
    task_id: str
    consultation_type: ConsultationType
    question: str               # What's being asked
    context: str = ""           # Relevant data / output to review
    timeout: float = 30.0
    priority: int = 1           # 0=low, 1=normal, 2=high


@dataclass
class ConsultationResponse:
    """A peer's response to a consultation."""
    from_agent: str
    to_agent: str
    task_id: str
    success: bool
    answer: str = ""
    error: str = ""
    latency_ms: float = 0.0
    cached: bool = False


@dataclass
class ConsultationAuditEntry:
    """Audit trail entry for a consultation."""
    timestamp: float
    from_agent: str
    to_agent: str
    task_id: str
    consultation_type: ConsultationType
    question: str
    success: bool
    latency_ms: float


class ConsultationProtocol:
    """
    Elite peer-to-peer consultation protocol for inter-agent help.

    Usage:
        protocol = ConsultationProtocol(max_consultations_per_task=3)

        # Agent A consults Agent B
        response = await protocol.consult(
            from_agent="code_agent",
            to_agent="security_agent",
            task_id="task_42",
            consultation_type=ConsultationType.REVIEW,
            question="Is this code safe?",
            context="def foo(): pass",
        )
    """

    # Prompt templates for each consultation type
    _PROMPT_TEMPLATES = {
        ConsultationType.REVIEW: (
            "You are consulting as {to_agent}. Another agent ({from_agent}) needs you to "
            "review their work. Be concise, critical, and actionable.\n\n"
            "Their request: {question}\n\n"
            "Context/Output to review:\n{context}\n\n"
            "Provide your review in 3-5 bullet points. Flag any issues clearly."
        ),
        ConsultationType.VERIFY: (
            "You are consulting as {to_agent}. Another agent ({from_agent}) needs you to "
            "verify something. Be precise and authoritative.\n\n"
            "Their question: {question}\n\n"
            "Context:\n{context}\n\n"
            "Respond with YES/NO and a brief explanation. Be definitive."
        ),
        ConsultationType.ADVISE: (
            "You are consulting as {to_agent}. Another agent ({from_agent}) seeks your "
            "expert advice. Be helpful and strategic.\n\n"
            "Their question: {question}\n\n"
            "Context:\n{context}\n\n"
            "Provide 2-3 actionable recommendations."
        ),
        ConsultationType.DELEGATE_PARTIAL: (
            "You are consulting as {to_agent}. Another agent ({from_agent}) is delegating "
            "a sub-task to you. Execute it thoroughly.\n\n"
            "The sub-task: {question}\n\n"
            "Additional context:\n{context}\n\n"
            "Complete the sub-task and return the result."
        ),
    }

    def __init__(self,
                 max_consultations_per_task: int = 3,
                 max_chain_depth: int = 2,
                 max_concurrent_per_agent: int = 2,
                 default_timeout: float = 30.0,
                 cache_ttl: float = 300.0):
        self.max_consultations_per_task = max_consultations_per_task
        self.max_chain_depth = max_chain_depth
        self.max_concurrent_per_agent = max_concurrent_per_agent
        self.default_timeout = default_timeout

        # Budget tracking: task_id -> count
        self._budgets: Dict[str, int] = {}
        self._budget_lock = asyncio.Lock()

        # Chain tracking: task_id -> list of (from, to) pairs
        self._chains: Dict[str, List[tuple[str, str]]] = {}
        self._chain_lock = asyncio.Lock()

        # Concurrency control: agent_name -> active count
        self._active_consults: Dict[str, int] = {}
        self._concurrency_lock = asyncio.Lock()

        # Result cache: hash -> response
        self._cache: Dict[str, ConsultationResponse] = {}
        self._cache_timestamps: Dict[str, float] = {}
        self._cache_lock = asyncio.Lock()
        self.cache_ttl = cache_ttl

        # Audit trail
        self._audit: List[ConsultationAuditEntry] = []
        self._audit_lock = asyncio.Lock()

    # ──────────────────────────────────────────────
    # Guard Rails
    # ──────────────────────────────────────────────

    async def _check_budget(self, task_id: str) -> bool:
        """Check if the task still has consultation budget remaining."""
        async with self._budget_lock:
            count = self._budgets.get(task_id, 0)
            return count < self.max_consultations_per_task

    async def _consume_budget(self, task_id: str) -> None:
        """Consume one consultation unit from the task's budget."""
        async with self._budget_lock:
            self._budgets[task_id] = self._budgets.get(task_id, 0) + 1

    async def _get_budget_remaining(self, task_id: str) -> int:
        """Get remaining consultation budget for a task."""
        async with self._budget_lock:
            return max(0, self.max_consultations_per_task - self._budgets.get(task_id, 0))

    async def _check_chain(self, from_agent: str, to_agent: str, task_id: str) -> bool:
        """
        Check if this consultation would create a cycle.
        Returns True if the consultation is safe to proceed.
        """
        async with self._chain_lock:
            chain = self._chains.setdefault(task_id, [])

            # Check if this exact pair already exists (A->B->A cycle)
            for existing_from, existing_to in chain:
                if existing_from == to_agent and existing_to == from_agent:
                    logger.warning("[consultation] Detected cycle: %s→%s→%s in task %s",
                                   from_agent, to_agent, from_agent, task_id)
                    return False

            # Check chain depth
            if len(chain) >= self.max_chain_depth:
                logger.warning("[consultation] Chain depth exceeded (%d) for task %s",
                               len(chain), task_id)
                return False

            return True

    async def _record_chain(self, from_agent: str, to_agent: str, task_id: str) -> None:
        """Record a consultation in the chain tracker."""
        async with self._chain_lock:
            self._chains.setdefault(task_id, []).append((from_agent, to_agent))

    async def _check_concurrency(self, agent_name: str) -> bool:
        """Check if the agent has capacity for another concurrent consultation."""
        async with self._concurrency_lock:
            return self._active_consults.get(agent_name, 0) < self.max_concurrent_per_agent

    async def _track_concurrency(self, agent_name: str, delta: int) -> None:
        """Track active consultation count for an agent."""
        async with self._concurrency_lock:
            self._active_consults[agent_name] = max(0, self._active_consults.get(agent_name, 0) + delta)

    # ──────────────────────────────────────────────
    # Core Consultation
    # ──────────────────────────────────────────────

    async def consult(self, *,
                      from_agent: str,
                      to_agent: str,
                      task_id: str,
                      question: str,
                      context: str = "",
                      consultation_type: ConsultationType = ConsultationType.REVIEW,
                      ai_handler=None,
                      orchestrator=None,
                      timeout: Optional[float] = None) -> ConsultationResponse:
        """
        Consult a peer agent.

        This is the primary entry point. It enforces:
          1. Budget check (max consultations per task)
          2. Anti-recursion (no A→B→A cycles)
          3. Concurrency limit (max concurrent per agent)
          4. Cache check (skip if identical consult was done recently)

        Args:
            from_agent: Name of the requesting agent
            to_agent: Name of the peer being consulted
            task_id: Task correlation ID
            question: What's being asked
            context: Relevant data / output to review
            consultation_type: Type of consultation (review, verify, advise, delegate_partial)
            ai_handler: For LLM-based consultation (required unless orchestrator dispatch is used)
            orchestrator: Optional — if set, dispatches through orchestrator instead of direct LLM
            timeout: Override default timeout

        Returns:
            ConsultationResponse with the peer's answer
        """
        effective_timeout = timeout or self.default_timeout

        # 1. Budget check
        if not await self._check_budget(task_id):
            logger.warning("[consultation] Budget exhausted for task %s (%d/%d)",
                           task_id, self._budgets.get(task_id, 0),
                           self.max_consultations_per_task)
            return ConsultationResponse(
                from_agent=to_agent, to_agent=from_agent, task_id=task_id,
                success=False, error=f"Consultation budget exhausted ({self.max_consultations_per_task}/task)",
            )

        # 2. Anti-recursion check
        if not await self._check_chain(from_agent, to_agent, task_id):
            return ConsultationResponse(
                from_agent=to_agent, to_agent=from_agent, task_id=task_id,
                success=False, error="Recursion detected: consult chain would cycle",
            )

        # 3. Concurrency check
        if not await self._check_concurrency(to_agent):
            logger.warning("[consultation] Agent '%s' at concurrency limit", to_agent)
            return ConsultationResponse(
                from_agent=to_agent, to_agent=from_agent, task_id=task_id,
                success=False, error=f"Agent {to_agent} at max concurrent consultations",
            )

        # 4. Cache check
        cache_key = self._cache_key(from_agent, to_agent, question, consultation_type)
        cached = await self._get_cached(cache_key)
        if cached:
            logger.debug("[consultation] Cache hit for %s→%s", from_agent, to_agent)
            return ConsultationResponse(
                from_agent=cached.from_agent, to_agent=cached.to_agent,
                task_id=task_id, success=True, answer=cached.answer,
                latency_ms=cached.latency_ms, cached=True,
            )

        # Execute the consultation
        await self._consume_budget(task_id)
        await self._record_chain(from_agent, to_agent, task_id)
        await self._track_concurrency(to_agent, +1)

        start_time = time.monotonic()
        try:
            if orchestrator:
                answer = await self._dispatch_via_orchestrator(
                    orchestrator, task_id, from_agent, to_agent,
                    question, context, consultation_type, effective_timeout,
                )
            elif ai_handler:
                answer = await self._direct_llm_consult(
                    ai_handler, from_agent, to_agent,
                    question, context, consultation_type, effective_timeout,
                )
            else:
                answer = "[Consultation failed: no ai_handler or orchestrator provided]"
                return ConsultationResponse(
                    from_agent=to_agent, to_agent=from_agent, task_id=task_id,
                    success=False, error="No consultation backend available",
                )

            latency_ms = (time.monotonic() - start_time) * 1000

            response = ConsultationResponse(
                from_agent=to_agent,
                to_agent=from_agent,
                task_id=task_id,
                success=True,
                answer=answer,
                latency_ms=round(latency_ms, 2),
            )

            # Cache the result
            await self._set_cached(cache_key, response)

            return response

        except asyncio.TimeoutError:
            latency_ms = (time.monotonic() - start_time) * 1000
            return ConsultationResponse(
                from_agent=to_agent, to_agent=from_agent, task_id=task_id,
                success=False, error=f"Consultation timed out after {effective_timeout}s",
                latency_ms=round(latency_ms, 2),
            )
        except Exception as e:
            latency_ms = (time.monotonic() - start_time) * 1000
            logger.error("[consultation] %s→%s failed: %s", from_agent, to_agent, e)
            return ConsultationResponse(
                from_agent=to_agent, to_agent=from_agent, task_id=task_id,
                success=False, error=str(e),
                latency_ms=round(latency_ms, 2),
            )
        finally:
            await self._track_concurrency(to_agent, -1)

            # Audit trail
            async with self._audit_lock:
                self._audit.append(ConsultationAuditEntry(
                    timestamp=time.monotonic(),
                    from_agent=from_agent,
                    to_agent=to_agent,
                    task_id=task_id,
                    consultation_type=consultation_type,
                    question=question[:200],
                    success=True,
                    latency_ms=round(latency_ms, 2),
                ))

    async def _direct_llm_consult(self, ai_handler, from_agent: str, to_agent: str,
                                   question: str, context: str,
                                   consultation_type: ConsultationType,
                                   timeout: float) -> str:
        """Direct LLM-based consultation (no orchestrator dispatch)."""
        prompt_template = self._PROMPT_TEMPLATES.get(
            consultation_type, self._PROMPT_TEMPLATES[ConsultationType.REVIEW]
        )
        prompt = prompt_template.format(
            from_agent=from_agent,
            to_agent=to_agent,
            question=question,
            context=context[:3000],  # Truncate to prevent context overflow
        )

        messages = [{"role": "user", "content": prompt}]

        async def _do_call():
            return await ai_handler.generate(messages, task="consultation")

        result = await asyncio.wait_for(_do_call(), timeout=timeout)
        return getattr(result, "text", str(result))

    async def _dispatch_via_orchestrator(self, orchestrator, task_id: str,
                                          from_agent: str, to_agent: str,
                                          question: str, context: str,
                                          consultation_type: ConsultationType,
                                          timeout: float) -> str:
        """Consult via orchestrator dispatch (heavier, but uses full agent pipeline)."""
        full_message = f"[Consultation from {from_agent} ({consultation_type.value})]\n\n{question}\n\n{context[:3000]}"

        async def _do_dispatch():
            return await orchestrator.dispatch(
                task_id=task_id,
                agent_name=to_agent,
                message=full_message,
                context={},
                is_background=False,
            )

        result = await asyncio.wait_for(_do_dispatch(), timeout=timeout)
        if getattr(result, "success", False):
            return result.result
        raise RuntimeError(result.error)

    # ──────────────────────────────────────────────
    # Cache
    # ──────────────────────────────────────────────

    @staticmethod
    def _cache_key(from_agent: str, to_agent: str, question: str, ctype: ConsultationType) -> str:
        raw = f"{from_agent}:{to_agent}:{ctype.value}:{question}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    async def _get_cached(self, key: str) -> Optional[ConsultationResponse]:
        async with self._cache_lock:
            if key not in self._cache:
                return None
            if time.monotonic() - self._cache_timestamps[key] > self.cache_ttl:
                del self._cache[key]
                del self._cache_timestamps[key]
                return None
            return self._cache[key]

    async def _set_cached(self, key: str, response: ConsultationResponse) -> None:
        async with self._cache_lock:
            self._cache[key] = response
            self._cache_timestamps[key] = time.monotonic()

    # ──────────────────────────────────────────────
    # Cleanup & Introspection
    # ──────────────────────────────────────────────

    async def cleanup_task(self, task_id: str) -> None:
        """Clean up all tracking state for a completed task."""
        async with self._budget_lock:
            self._budgets.pop(task_id, None)
        async with self._chain_lock:
            self._chains.pop(task_id, None)

    async def get_stats(self) -> Dict[str, Any]:
        """Get consultation protocol health metrics."""
        async with self._budget_lock:
            budget_stats = dict(self._budgets)
        async with self._audit_lock:
            audit_count = len(self._audit)
        async with self._cache_lock:
            cache_size = len(self._cache)
        async with self._concurrency_lock:
            active = dict(self._active_consults)

        return {
            "active_budgets": budget_stats,
            "total_consultations_audited": audit_count,
            "cache_size": cache_size,
            "active_concurrent": active,
            "max_consultations_per_task": self.max_consultations_per_task,
            "max_chain_depth": self.max_chain_depth,
        }

    async def get_audit_trail(self, task_id: Optional[str] = None) -> List[dict]:
        """Get consultation audit trail, optionally filtered by task_id."""
        async with self._audit_lock:
            entries = self._audit
            if task_id:
                entries = [e for e in entries if e.task_id == task_id]
            return [
                {
                    "timestamp": e.timestamp,
                    "from": e.from_agent,
                    "to": e.to_agent,
                    "type": e.consultation_type.value,
                    "question": e.question,
                    "success": e.success,
                    "latency_ms": e.latency_ms,
                }
                for e in entries
            ]
