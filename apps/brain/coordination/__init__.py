"""
Makima v7.2 — Elite Agent Coordination Ecosystem

Provides the primitives and patterns for multi-agent coordination:
  - SharedBlackboard: per-task shared workspace with TTL, versioning, snapshots
  - EventBus: inter-agent pub/sub with pattern matching and backpressure
  - CapabilityRegistry: dynamic agent capability directory with semantic matching
  - ConsultationProtocol: peer-to-peer agent help with budget guardrails
  - CoordinationHub: central facade wiring all primitives together
  - Swarm patterns: reusable multi-agent workflows (Research, Build, Audit, DataPipeline)
"""

from .blackboard import SharedBlackboard, BlackboardEntry, BlackboardSnapshot, MergeStrategy
from .event_bus import EventBus, AgentEvent, EventPriority, Subscription, DeadLetterEntry
from .capability_registry import (
    CapabilityRegistry, AgentProfile, AgentAvailability, AgentMatch,
)
from .consultation import (
    ConsultationProtocol, ConsultationType, ConsultationRequest, ConsultationResponse,
)
from .hub import CoordinationHub
from .swarm import (
    BaseSwarm, ResearchSwarm, BuildSwarm, AuditSwarm, DataPipelineSwarm,
    SwarmResult, PhaseResult, SwarmPhase, create_swarm, list_swarms,
)

__all__ = [
    # Blackboard
    "SharedBlackboard", "BlackboardEntry", "BlackboardSnapshot", "MergeStrategy",
    # Event Bus
    "EventBus", "AgentEvent", "EventPriority", "Subscription", "DeadLetterEntry",
    # Capability Registry
    "CapabilityRegistry", "AgentProfile", "AgentAvailability", "AgentMatch",
    # Consultation
    "ConsultationProtocol", "ConsultationType", "ConsultationRequest", "ConsultationResponse",
    # Hub
    "CoordinationHub",
    # Swarms
    "BaseSwarm", "ResearchSwarm", "BuildSwarm", "AuditSwarm", "DataPipelineSwarm",
    "SwarmResult", "PhaseResult", "SwarmPhase", "create_swarm", "list_swarms",
]
