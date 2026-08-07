"""
Makima v7.2 — Elite Coordination Hub

Central facade that wires together all coordination primitives:
  - SharedBlackboard: per-task shared workspace
  - EventBus: inter-agent pub/sub
  - CapabilityRegistry: dynamic agent capability directory
  - ConsultationProtocol: peer-to-peer agent help

The orchestrator instantiates one hub and passes it to all agents.
Agents access primitives via `self.coordination.blackboard`, etc.

Provides:
  - Single entry point for all coordination needs
  - Unified health dashboard (get_stats())
  - Task lifecycle hooks (on_task_start, on_task_complete)
  - Swarm pattern factory
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from .blackboard import SharedBlackboard
from .event_bus import EventBus, AgentEvent, EventPriority
from .capability_registry import CapabilityRegistry, AgentProfile, AgentAvailability
from .consultation import ConsultationProtocol, ConsultationType
from .swarm import BaseSwarm, create_swarm, list_swarms, SwarmResult

logger = logging.getLogger("makima.coordination.hub")


class CoordinationHub:
    """
    Central facade for the entire agent coordination ecosystem.

    Usage (in orchestrator):
        hub = CoordinationHub()
        await hub.register_agent(AgentProfile(
            name="research_agent",
            description="Multi-hop web research",
            capabilities=["web_search", "query_decomposition"],
            tools=["web_search", "web_fetch"],
            tags=["research", "web"],
        ))

        # Pass to agents via constructor
        agent = ResearchAgent(
            ai_handler=..., memory=...,
            coordination=hub,
        )
    """

    def __init__(self,
                 max_entries_per_task: int = 500,
                 max_queue_depth: int = 100,
                 max_consultations_per_task: int = 3,
                 ai_handler=None):
        self.blackboard = SharedBlackboard(max_entries_per_task=max_entries_per_task)
        self.event_bus = EventBus(max_queue_depth=max_queue_depth)
        self.registry = CapabilityRegistry()
        self.consultation = ConsultationProtocol(
            max_consultations_per_task=max_consultations_per_task
        )
        self._ai_handler = ai_handler
        self._orchestrator = None  # Set by orchestrator after construction

    def set_orchestrator(self, orchestrator) -> None:
        """Link the orchestrator for swarm dispatch and agent lifecycle."""
        self._orchestrator = orchestrator

    def set_ai_handler(self, ai_handler) -> None:
        """Set the AI handler for consultation LLM calls."""
        self._ai_handler = ai_handler

    # ──────────────────────────────────────────────
    # Agent Lifecycle Hooks
    # ──────────────────────────────────────────────

    async def on_agent_register(self, profile: AgentProfile) -> None:
        """Called when an agent is initialized. Registers its capability profile."""
        await self.registry.register(profile)
        await self.event_bus.publish_simple(
            event_type="lifecycle.agent_registered",
            source=profile.name,
            payload={"description": profile.description, "tags": profile.tags},
        )

    async def on_task_start(self, task_id: str, agent_name: str) -> None:
        """Called when a task is dispatched to an agent."""
        await self.registry.set_availability(agent_name, AgentAvailability.RUNNING)
        await self.event_bus.publish_simple(
            event_type="task.started",
            source=agent_name,
            payload={"task_id": task_id},
            task_id=task_id,
        )

    async def on_task_complete(self, task_id: str, agent_name: str,
                               success: bool, latency_s: float) -> None:
        """Called when an agent finishes a task (success or failure)."""
        await self.registry.set_availability(
            agent_name,
            AgentAvailability.IDLE if success else AgentAvailability.ERROR
        )
        await self.registry.record_completion(agent_name, latency_s)
        await self.event_bus.publish_simple(
            event_type="task.completed" if success else "task.failed",
            source=agent_name,
            payload={"task_id": task_id, "success": success, "latency_s": round(latency_s, 2)},
            task_id=task_id,
        )

    async def on_task_cancelled(self, task_id: str, agent_name: str) -> None:
        """Called when a task is cancelled."""
        await self.registry.set_availability(agent_name, AgentAvailability.IDLE)
        await self.event_bus.publish_simple(
            event_type="task.cancelled",
            source=agent_name,
            payload={"task_id": task_id},
            task_id=task_id,
        )

    async def cleanup_task(self, task_id: str) -> None:
        """Clean up all coordination state for a completed task."""
        await self.blackboard.clear_task(task_id)
        await self.consultation.cleanup_task(task_id)
        logger.debug("[hub] Cleaned up coordination state for task %s", task_id)

    # ──────────────────────────────────────────────
    # Swarm Factory & Multi-Step Execution
    # ──────────────────────────────────────────────

    def create_swarm(self, name: str, **kwargs) -> Optional[BaseSwarm]:
        """Create a swarm pattern instance by name."""
        if not self._orchestrator:
            logger.error("[hub] Cannot create swarm: orchestrator not linked")
            return None
        return create_swarm(
            name=name,
            orchestrator=self._orchestrator,
            blackboard=self.blackboard,
            event_bus=self.event_bus,
            ai_handler=self._ai_handler,
            **kwargs,
        )

    async def execute(self, task_id: str, message: str, context: dict = None, entities: dict = None) -> Optional[str]:
        """
        Execute a multi-step task via swarm patterns or agent coordination.
        Called by CommandRouter when intent is MULTI_STEP.
        """
        if not self._orchestrator:
            logger.warning("[hub] Cannot execute multi-step task: orchestrator not linked")
            return None

        msg_lower = (message or "").lower()
        swarm_name = None
        if any(k in msg_lower for k in ["code", "build", "refactor", "fix", "program"]):
            swarm_name = "build"
        elif any(k in msg_lower for k in ["audit", "scan", "vulnerability", "security"]):
            swarm_name = "audit"
        elif any(k in msg_lower for k in ["data", "csv", "chart", "metrics", "analysis"]):
            swarm_name = "data_pipeline"
        elif any(k in msg_lower for k in ["research", "compare", "briefing", "synthesize", "report", "ppt", "slide"]):
            swarm_name = "research"

        if not swarm_name:
            logger.info("[hub] No specialized swarm matched, falling back to dynamic DAG planner")
            return None

        swarm = self.create_swarm(swarm_name)
        if swarm:
            try:
                res = await swarm.execute(task_id=task_id, message=message, context=context)
                if res and res.synthesis:
                    return res.synthesis
            except Exception as e:
                logger.error("[hub] Swarm '%s' execution failed: %s", swarm_name, e)
        return None

    # ──────────────────────────────────────────────
    # Unified Health Dashboard
    # ──────────────────────────────────────────────

    async def get_stats(self) -> Dict[str, Any]:
        """Get health metrics from all coordination subsystems."""
        bb_stats = await self.blackboard.get_stats()
        bus_stats = await self.event_bus.get_stats()
        reg_stats = await self.registry.get_stats()
        consult_stats = await self.consultation.get_stats()

        return {
            "blackboard": bb_stats,
            "event_bus": bus_stats,
            "capability_registry": reg_stats,
            "consultation": consult_stats,
            "available_swarms": list_swarms(),
        }

    async def shutdown(self) -> None:
        """Gracefully shut down all coordination subsystems."""
        await self.event_bus.shutdown()
        logger.info("[hub] Coordination hub shut down")
