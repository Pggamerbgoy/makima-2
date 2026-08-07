"""
Makima v7.2 — Elite Swarm Patterns

Reusable multi-agent collaboration workflows. Each swarm wires agents together
for a specific pipeline using the SharedBlackboard for intermediate state,
EventBus for phase notifications, and the Orchestrator for dispatch.

Swarms:
  - ResearchSwarm: research → analyze → report (deep multi-source research)
  - BuildSwarm: research → code → test (build with verification)
  - AuditSwarm: scan → report → fix (security audit with remediation)
  - DataPipelineSwarm: extract → transform → visualize (data pipeline)

Each swarm provides:
  - Phase-based execution with blackboard intermediates
  - Event publishing on phase transitions
  - Rollback support via blackboard snapshots
  - Adaptive retry on phase failures
  - Final synthesis via LLM
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("makima.coordination.swarm")


class SwarmPhase(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class PhaseResult:
    """Result from a single swarm phase."""
    phase_name: str
    agent_name: str
    success: bool
    output: str = ""
    error: str = ""
    duration_s: float = 0.0
    retries: int = 0


@dataclass
class SwarmResult:
    """Final result from a swarm execution."""
    swarm_name: str
    task_id: str
    phase: SwarmPhase
    phases: List[PhaseResult] = field(default_factory=list)
    synthesis: str = ""
    duration_s: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.phase == SwarmPhase.COMPLETED


class BaseSwarm:
    """
    Base class for swarm patterns. Subclasses define their phases and agent mappings.

    Provides:
      - Phase execution with blackboard read/write
      - Event publishing on transitions
      - Rollback on failure
      - Retry with reflexion on errors
    """

    SWARM_NAME: str = "base"
    DESCRIPTION: str = "Base swarm"

    def __init__(self, orchestrator, blackboard, event_bus, ai_handler,
                 max_retries: int = 1, phase_timeout: float = 120.0):
        self.orchestrator = orchestrator
        self.blackboard = blackboard
        self.event_bus = event_bus
        self.ai_handler = ai_handler
        self.max_retries = max_retries
        self.phase_timeout = phase_timeout

    async def execute(self, task_id: str, message: str, context: dict = None) -> SwarmResult:
        """Execute the swarm pipeline. Override define_phases() in subclasses."""
        start_time = time.monotonic()
        context = context or {}
        phases = self.define_phases()

        result = SwarmResult(swarm_name=self.SWARM_NAME, task_id=task_id, phase=SwarmPhase.RUNNING)

        # Take blackboard snapshot for rollback
        snapshot = None
        if self.blackboard:
            snapshot = await self.blackboard.snapshot(task_id)

        # Publish swarm start
        await self._publish_event(task_id, f"swarm.{self.SWARM_NAME}.started", {"message": message[:200]})

        for phase_def in phases:
            phase_name = phase_def["name"]
            agent_name = phase_def["agent"]
            instruction_template = phase_def["instruction"]

            # Build instruction with context from blackboard + previous phases
            instruction = await self._build_instruction(task_id, instruction_template, message, result.phases)

            # Execute with retry
            phase_result = await self._execute_phase(
                task_id, phase_name, agent_name, instruction
            )
            result.phases.append(phase_result)

            if not phase_result.success:
                logger.error("[swarm:%s] Phase '%s' failed: %s", self.SWARM_NAME, phase_name, phase_result.error)
                result.phase = SwarmPhase.FAILED

                # Rollback if snapshot available
                if snapshot and self.blackboard:
                    try:
                        await self.blackboard.restore(snapshot)
                        result.phase = SwarmPhase.ROLLED_BACK
                    except Exception as e:
                        logger.error("[swarm:%s] Rollback failed: %s", self.SWARM_NAME, e)

                break

            # Store phase result in blackboard for downstream phases
            if self.blackboard:
                await self.blackboard.write(
                    task_id, f"swarm.{self.SWARM_NAME}.{phase_name}.output",
                    phase_result.output, agent=agent_name,
                    metadata={"phase": phase_name, "duration_s": phase_result.duration_s},
                )

            await self._publish_event(task_id, f"swarm.{self.SWARM_NAME}.{phase_name}.done",
                                      {"output_preview": phase_result.output[:200]})

        # If all phases succeeded, synthesize
        if all(p.success for p in result.phases):
            result.phase = SwarmPhase.COMPLETED
            result.synthesis = await self._synthesize(task_id, message, result.phases)
        elif result.phase == SwarmPhase.RUNNING:
            result.phase = SwarmPhase.COMPLETED
            result.synthesis = await self._synthesize(task_id, message, result.phases)

        result.duration_s = time.monotonic() - start_time

        await self._publish_event(task_id, f"swarm.{self.SWARM_NAME}.{result.phase.value}",
                                  {"synthesis_preview": result.synthesis[:200]})

        return result

    async def _execute_phase(self, task_id: str, phase_name: str,
                             agent_name: str, instruction: str) -> PhaseResult:
        """Execute a single phase with retry logic."""
        retries = 0
        last_error = ""

        while retries <= self.max_retries:
            start = time.monotonic()
            try:
                res = await asyncio.wait_for(
                    self.orchestrator.dispatch(
                        task_id=task_id,
                        agent_name=agent_name,
                        message=instruction,
                        context={},
                    ),
                    timeout=self.phase_timeout,
                )

                duration = time.monotonic() - start

                if getattr(res, "success", False):
                    return PhaseResult(
                        phase_name=phase_name, agent_name=agent_name,
                        success=True, output=res.result,
                        duration_s=duration, retries=retries,
                    )
                else:
                    last_error = getattr(res, "error", "Unknown error")
                    logger.warning("[swarm:%s] Phase '%s' attempt %d failed: %s",
                                   self.SWARM_NAME, phase_name, retries + 1, last_error)

            except asyncio.TimeoutError:
                duration = time.monotonic() - start
                last_error = f"Phase timed out after {self.phase_timeout}s"
                logger.warning("[swarm:%s] Phase '%s' timed out (attempt %d)",
                               self.SWARM_NAME, phase_name, retries + 1)
            except Exception as e:
                duration = time.monotonic() - start
                last_error = str(e)
                logger.error("[swarm:%s] Phase '%s' exception: %s", self.SWARM_NAME, phase_name, e)

            retries += 1

        return PhaseResult(
            phase_name=phase_name, agent_name=agent_name,
            success=False, error=last_error,
            duration_s=time.monotonic() - start, retries=retries,
        )

    async def _build_instruction(self, task_id: str, template: str,
                                 original_message: str,
                                 previous_phases: List[PhaseResult]) -> str:
        """Build the instruction for a phase, injecting context from blackboard and previous phases."""
        instruction = template.replace("{{original_message}}", original_message)

        # Inject previous phase outputs
        phase_outputs = "\n\n".join([
            f"[Phase: {p.phase_name} ({p.agent_name})]:\n{p.output[:2000]}"
            for p in previous_phases if p.success
        ])
        instruction = instruction.replace("{{previous_outputs}}", phase_outputs)

        # Inject blackboard context
        if self.blackboard:
            bb_context = await self.blackboard.read_all(task_id, prefix="swarm.")
            bb_str = "\n".join([f"- {k}: {str(v)[:500]}" for k, v in bb_context.items()])
            instruction = instruction.replace("{{blackboard_context}}", bb_str)

        return instruction

    async def _synthesize(self, task_id: str, message: str, phases: List[PhaseResult]) -> str:
        """Synthesize final output from all phase results."""
        phase_summaries = "\n\n".join([
            f"**{p.phase_name}** ({p.agent_name}): {'✓' if p.success else '✗'}\n{p.output[:1500] if p.success else p.error}"
            for p in phases
        ])

        prompt = (
            f"Original Request: {message}\n\n"
            f"Swarm: {self.SWARM_NAME} ({self.DESCRIPTION})\n\n"
            f"Phase Results:\n{phase_summaries}\n\n"
            f"Synthesize a comprehensive, professional final response that directly "
            f"answers the user's request using the phase results. Use Markdown formatting."
        )

        try:
            response = await self.ai_handler.generate(
                [{"role": "system", "content": f"You are Makima's {self.SWARM_NAME} swarm synthesizer."},
                 {"role": "user", "content": prompt}],
                task="swarm_synthesis",
            )
            return getattr(response, "text", str(response))
        except Exception as e:
            logger.error("[swarm:%s] Synthesis failed: %s", self.SWARM_NAME, e)
            return f"Swarm execution completed but synthesis failed. Raw results:\n{phase_summaries}"

    async def _publish_event(self, task_id: str, event_type: str, payload: dict) -> None:
        """Publish a swarm event to the bus."""
        if not self.event_bus:
            return
        try:
            await self.event_bus.publish_simple(
                event_type=event_type,
                source=f"swarm:{self.SWARM_NAME}",
                payload=payload,
                task_id=task_id,
            )
        except Exception as e:
            logger.debug("[swarm:%s] Event publish failed: %s", self.SWARM_NAME, e)

    def define_phases(self) -> List[dict]:
        """Override in subclasses. Returns list of phase definitions."""
        return []


# ──────────────────────────────────────────────
# Concrete Swarm Patterns
# ──────────────────────────────────────────────


class ResearchSwarm(BaseSwarm):
    """
    Deep multi-source research swarm.
    Phases: research → analyze → report
    """
    SWARM_NAME = "research"
    DESCRIPTION = "Multi-source deep research with analysis and executive report"

    def define_phases(self) -> List[dict]:
        return [
            {
                "name": "gather",
                "agent": "research_agent",
                "instruction": (
                    "Research the following topic thoroughly. Search for authoritative sources "
                    "(academic, official, technical documentation). Return raw findings with source URLs.\n\n"
                    "Topic: {{original_message}}\n\n"
                    "{{blackboard_context}}"
                ),
            },
            {
                "name": "analyze",
                "agent": "data_analyst_agent",
                "instruction": (
                    "Analyze the research findings from the gather phase. Identify patterns, "
                    "contradictions, key data points, and knowledge gaps.\n\n"
                    "Topic: {{original_message}}\n\n"
                    "{{previous_outputs}}\n\n"
                    "{{blackboard_context}}"
                ),
            },
            {
                "name": "report",
                "agent": "document_agent",
                "instruction": (
                    "Generate an executive research report based on the analysis.\n\n"
                    "Topic: {{original_message}}\n\n"
                    "{{previous_outputs}}\n\n"
                    "{{blackboard_context}}"
                ),
            },
        ]


class BuildSwarm(BaseSwarm):
    """
    Build with verification swarm.
    Phases: research → code → test
    """
    SWARM_NAME = "build"
    DESCRIPTION = "Research-backed code generation with automated testing"

    def define_phases(self) -> List[dict]:
        return [
            {
                "name": "research",
                "agent": "research_agent",
                "instruction": (
                    "Research the best practices, patterns, and APIs needed for the following build task.\n\n"
                    "Task: {{original_message}}\n\n"
                    "{{blackboard_context}}"
                ),
            },
            {
                "name": "code",
                "agent": "code_agent",
                "instruction": (
                    "Implement the solution based on the research findings. Write clean, tested, well-documented code.\n\n"
                    "Task: {{original_message}}\n\n"
                    "Research:\n{{previous_outputs}}\n\n"
                    "{{blackboard_context}}"
                ),
            },
            {
                "name": "review",
                "agent": "security_agent",
                "instruction": (
                    "Review the generated code for security vulnerabilities, performance issues, "
                    "and best-practice violations.\n\n"
                    "Task: {{original_message}}\n\n"
                    "Code Output:\n{{previous_outputs}}\n\n"
                    "{{blackboard_context}}"
                ),
            },
        ]


class AuditSwarm(BaseSwarm):
    """
    Security audit swarm.
    Phases: scan → report → fix
    """
    SWARM_NAME = "audit"
    DESCRIPTION = "Security scan with vulnerability reporting and remediation"

    def define_phases(self) -> List[dict]:
        return [
            {
                "name": "scan",
                "agent": "security_agent",
                "instruction": (
                    "Perform a comprehensive security scan on the target.\n\n"
                    "Target: {{original_message}}\n\n"
                    "{{blackboard_context}}"
                ),
            },
            {
                "name": "report",
                "agent": "document_agent",
                "instruction": (
                    "Generate a detailed security audit report from the scan findings.\n\n"
                    "Target: {{original_message}}\n\n"
                    "Scan Results:\n{{previous_outputs}}\n\n"
                    "{{blackboard_context}}"
                ),
            },
            {
                "name": "remediate",
                "agent": "code_agent",
                "instruction": (
                    "Based on the security audit findings, provide code fixes and remediation "
                    "steps for any identified vulnerabilities.\n\n"
                    "Target: {{original_message}}\n\n"
                    "{{previous_outputs}}\n\n"
                    "{{blackboard_context}}"
                ),
            },
        ]


class DataPipelineSwarm(BaseSwarm):
    """
    Data processing pipeline swarm.
    Phases: extract → transform → visualize
    """
    SWARM_NAME = "data_pipeline"
    DESCRIPTION = "End-to-end data extraction, transformation, and visualization"

    def define_phases(self) -> List[dict]:
        return [
            {
                "name": "extract",
                "agent": "browser_agent",
                "instruction": (
                    "Extract data from the specified source.\n\n"
                    "Source: {{original_message}}\n\n"
                    "{{blackboard_context}}"
                ),
            },
            {
                "name": "transform",
                "agent": "data_analyst_agent",
                "instruction": (
                    "Clean, transform, and analyze the extracted data. Generate statistical "
                    "summaries and key insights.\n\n"
                    "Original Request: {{original_message}}\n\n"
                    "Raw Data:\n{{previous_outputs}}\n\n"
                    "{{blackboard_context}}"
                ),
            },
            {
                "name": "visualize",
                "agent": "document_agent",
                "instruction": (
                    "Create a comprehensive data report with the analysis results.\n\n"
                    "Original Request: {{original_message}}\n\n"
                    "Analysis:\n{{previous_outputs}}\n\n"
                    "{{blackboard_context}}"
                ),
            },
        ]


# ──────────────────────────────────────────────
# Swarm Registry
# ──────────────────────────────────────────────

SWARM_REGISTRY: Dict[str, type] = {
    "research": ResearchSwarm,
    "build": BuildSwarm,
    "audit": AuditSwarm,
    "data_pipeline": DataPipelineSwarm,
}


def create_swarm(name: str, orchestrator, blackboard, event_bus, ai_handler,
                 **kwargs) -> Optional[BaseSwarm]:
    """Factory function to instantiate a swarm by name."""
    cls = SWARM_REGISTRY.get(name)
    if not cls:
        logger.error("[swarm] Unknown swarm pattern: '%s'. Available: %s", name, list(SWARM_REGISTRY.keys()))
        return None
    return cls(orchestrator=orchestrator, blackboard=blackboard,
               event_bus=event_bus, ai_handler=ai_handler, **kwargs)


def list_swarms() -> Dict[str, str]:
    """List all available swarm patterns with descriptions."""
    return {name: cls.DESCRIPTION for name, cls in SWARM_REGISTRY.items()}
