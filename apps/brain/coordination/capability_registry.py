"""
Makima v7.2 — Elite Capability Registry

Dynamic agent capability directory. Each agent self-registers its capabilities,
tools, and specializations on init. Any agent can query "who can do X?" at runtime.
Replaces the Commander's hardcoded agent list with a dynamic, semantic-matching
registry.

Features:
  - Self-registration: agents register capabilities, tools, tags on init
  - Semantic matching: Jaccard + TF-style scoring to find best-fit agents for a query
  - Availability tracking: idle/running/error state synced with orchestrator
  - Tool inventory: which tools each agent can call
  - Specialization tags: fine-grained labels (e.g., "csv", "docker", "voice")
  - Dynamic prompt generation: builds the Commander's system prompt from registry
  - Async-safe with fine-grained locking
  - Weighted scoring: capability_match (0.5) + tool_match (0.3) + tag_match (0.2)
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("makima.coordination.capability_registry")


class AgentAvailability(str, Enum):
    """Runtime availability state synced with orchestrator."""
    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class AgentProfile:
    """
    Full capability profile for a single agent.
    Self-registered by the agent on init or updated by the orchestrator.
    """
    name: str
    description: str
    capabilities: List[str] = field(default_factory=list)  # e.g., ["web_search", "code_generation"]
    tools: List[str] = field(default_factory=list)         # e.g., ["web_search", "shell", "file_read"]
    tags: List[str] = field(default_factory=list)          # e.g., ["csv", "docker", "voice", "security"]
    availability: AgentAvailability = AgentAvailability.UNKNOWN
    last_heartbeat: float = field(default_factory=time.monotonic)
    tasks_completed: int = 0
    avg_latency_s: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Internal: tokenized indices for fast matching
    _capability_tokens: Set[str] = field(default_factory=set, repr=False)
    _tool_tokens: Set[str] = field(default_factory=set, repr=False)
    _tag_tokens: Set[str] = field(default_factory=set, repr=False)
    _description_tokens: Set[str] = field(default_factory=set, repr=False)

    def index_tokens(self) -> None:
        """Pre-tokenize all fields for fast semantic matching."""
        self._capability_tokens = self._tokenize(" ".join(self.capabilities))
        self._tool_tokens = self._tokenize(" ".join(self.tools))
        self._tag_tokens = self._tokenize(" ".join(self.tags))
        self._description_tokens = self._tokenize(self.description)

    @staticmethod
    def _tokenize(text: str) -> Set[str]:
        """Tokenize text into lowercase words for Jaccard-style matching."""
        return set(re.findall(r'\b\w+\b', text.lower()))

    @property
    def is_available(self) -> bool:
        return self.availability in (AgentAvailability.IDLE, AgentAvailability.UNKNOWN)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "capabilities": self.capabilities,
            "tools": self.tools,
            "tags": self.tags,
            "availability": self.availability.value,
            "tasks_completed": self.tasks_completed,
            "avg_latency_s": round(self.avg_latency_s, 2),
        }


@dataclass
class AgentMatch:
    """Result of a capability query — a scored match."""
    profile: AgentProfile
    score: float           # 0.0 - 1.0 composite score
    capability_score: float
    tool_score: float
    tag_score: float
    desc_score: float


class CapabilityRegistry:
    """
    Elite dynamic agent capability directory.

    Usage:
        registry = CapabilityRegistry()
        await registry.register(AgentProfile(
            name="research_agent",
            description="Multi-hop web research",
            capabilities=["web_search", "query_decomposition", "credibility_scoring"],
            tools=["web_search", "web_fetch"],
            tags=["research", "web", "analysis"],
        ))

        # Find best agent for a task
        matches = await registry.find("search the web for AI papers")
        best = matches[0] if matches else None

        # Update availability
        await registry.set_availability("research_agent", AgentAvailability.RUNNING)

        # Generate Commander's prompt
        prompt = await registry.build_commander_prompt()
    """

    def __init__(self):
        self._profiles: Dict[str, AgentProfile] = {}
        self._lock = asyncio.Lock()

        # Scoring weights (tunable)
        self._w_capability = 0.35
        self._w_tool = 0.25
        self._w_tag = 0.20
        self._w_description = 0.20

    # ──────────────────────────────────────────────
    # Registration & Updates
    # ──────────────────────────────────────────────

    async def register(self, profile: AgentProfile) -> None:
        """Register an agent's capability profile. Indexes tokens for fast lookup."""
        async with self._lock:
            profile.index_tokens()
            self._profiles[profile.name] = profile
            logger.info("[registry] Registered agent '%s' with %d capabilities, %d tools, %d tags",
                        profile.name, len(profile.capabilities),
                        len(profile.tools), len(profile.tags))

    async def update(self, name: str, **kwargs) -> None:
        """Update specific fields of a registered agent's profile."""
        async with self._lock:
            if name not in self._profiles:
                logger.warning("[registry] Cannot update unknown agent '%s'", name)
                return
            profile = self._profiles[name]
            for key, value in kwargs.items():
                if hasattr(profile, key):
                    setattr(profile, key, value)
            # Re-index tokens if relevant fields changed
            if any(k in kwargs for k in ("capabilities", "tools", "tags", "description")):
                profile.index_tokens()

    async def set_availability(self, name: str, availability: AgentAvailability) -> None:
        """Update an agent's availability state (called by orchestrator on dispatch/complete)."""
        async with self._lock:
            if name in self._profiles:
                self._profiles[name].availability = availability
                self._profiles[name].last_heartbeat = time.monotonic()

    async def record_completion(self, name: str, latency_s: float) -> None:
        """Record a task completion with latency for performance tracking."""
        async with self._lock:
            if name not in self._profiles:
                return
            profile = self._profiles[name]
            n = profile.tasks_completed
            # Exponential moving average for latency
            profile.avg_latency_s = (profile.avg_latency_s * n + latency_s) / (n + 1)
            profile.tasks_completed = n + 1
            profile.availability = AgentAvailability.IDLE
            profile.last_heartbeat = time.monotonic()

    async def heartbeat(self, name: str) -> None:
        """Update last heartbeat timestamp."""
        async with self._lock:
            if name in self._profiles:
                self._profiles[name].last_heartbeat = time.monotonic()

    # ──────────────────────────────────────────────
    # Semantic Matching
    # ──────────────────────────────────────────────

    def _jaccard(self, set1: Set[str], set2: Set[str]) -> float:
        """Zero-dependency Jaccard similarity between two token sets."""
        if not set1 or not set2:
            return 0.0
        intersection = set1 & set2
        union = set1 | set2
        return len(intersection) / len(union)

    def _overlap_score(self, query_tokens: Set[str], field_tokens: Set[str]) -> float:
        """
        Overlap score: what fraction of query tokens appear in the field.
        Better than Jaccard for short queries vs long profiles.
        """
        if not query_tokens or not field_tokens:
            return 0.0
        overlap = query_tokens & field_tokens
        return len(overlap) / len(query_tokens)

    async def find(self, query: str, *,
                   only_available: bool = True,
                   top_k: int = 3) -> List[AgentMatch]:
        """
        Find the best-fit agents for a natural-language query.

        Uses multi-dimensional scoring:
          - Capability match (35%): Jaccard between query and agent capabilities
          - Tool match (25%): overlap of query tokens with agent tool names
          - Tag match (20%): overlap with specialization tags
          - Description match (20%): semantic overlap with agent description

        Args:
            query: Natural-language description of what needs to be done
            only_available: If True, only return agents that are idle/available
            top_k: Number of top matches to return

        Returns:
            List of AgentMatch sorted by descending composite score
        """
        query_tokens = AgentProfile._tokenize(query)
        if not query_tokens:
            return []

        matches: List[AgentMatch] = []

        async with self._lock:
            for name, profile in self._profiles.items():
                if only_available and not profile.is_available:
                    continue

                # Multi-dimensional scoring
                cap_score = max(
                    self._jaccard(query_tokens, profile._capability_tokens),
                    self._overlap_score(query_tokens, profile._capability_tokens)
                )
                tool_score = max(
                    self._jaccard(query_tokens, profile._tool_tokens),
                    self._overlap_score(query_tokens, profile._tool_tokens)
                )
                tag_score = max(
                    self._jaccard(query_tokens, profile._tag_tokens),
                    self._overlap_score(query_tokens, profile._tag_tokens)
                )
                desc_score = self._overlap_score(query_tokens, profile._description_tokens)

                composite = (
                    self._w_capability * cap_score +
                    self._w_tool * tool_score +
                    self._w_tag * tag_score +
                    self._w_description * desc_score
                )

                if composite > 0.0:
                    matches.append(AgentMatch(
                        profile=profile,
                        score=composite,
                        capability_score=cap_score,
                        tool_score=tool_score,
                        tag_score=tag_score,
                        desc_score=desc_score,
                    ))

        # Sort by score descending, break ties by avg_latency (faster agents first)
        matches.sort(key=lambda m: (m.score, -m.profile.avg_latency_s), reverse=True)
        return matches[:top_k]

    async def find_by_capability(self, capability: str, *,
                                  only_available: bool = True) -> List[str]:
        """Find agents that have a specific capability. Returns agent names."""
        capability_tokens = AgentProfile._tokenize(capability)
        results = []

        async with self._lock:
            for name, profile in self._profiles.items():
                if only_available and not profile.is_available:
                    continue
                # Check if any capability token overlaps
                overlap = capability_tokens & profile._capability_tokens
                if overlap:
                    results.append(name)
        return results

    async def find_by_tool(self, tool_name: str, *,
                           only_available: bool = True) -> List[str]:
        """Find agents that can call a specific tool. Returns agent names."""
        async with self._lock:
            return [
                name for name, profile in self._profiles.items()
                if tool_name in profile.tools and (not only_available or profile.is_available)
            ]

    async def find_by_tag(self, tag: str, *,
                          only_available: bool = True) -> List[str]:
        """Find agents with a specific specialization tag."""
        async with self._lock:
            return [
                name for name, profile in self._profiles.items()
                if tag.lower() in {t.lower() for t in profile.tags}
                and (not only_available or profile.is_available)
            ]

    # ──────────────────────────────────────────────
    # Commander Integration
    # ──────────────────────────────────────────────

    async def build_commander_prompt(self, exclude_commander: bool = True, query: str = "") -> str:
        """
        Build the dynamic agent capability section for CommanderAgent's system prompt.
        Replaces the hardcoded agent list and injects vast situational workflows.
        """
        lines = []
        async with self._lock:
            for name, profile in sorted(self._profiles.items()):
                if exclude_commander and name == "commander":
                    continue
                caps = ", ".join(profile.capabilities[:5]) if profile.capabilities else "general"
                tags = ", ".join(profile.tags[:3]) if profile.tags else ""
                avail = "✓" if profile.is_available else "✗"

                line = f"- **{name}** [{avail}]: {profile.description}"
                if caps:
                    line += f" | Capabilities: {caps}"
                if tags:
                    line += f" | Tags: {tags}"
                if profile.tools:
                    line += f" | Tools: {', '.join(profile.tools[:5])}"
                lines.append(line)

        if not lines:
            lines = ["- No agents registered yet"]

        try:
            from .agent_situational_encyclopedia import get_situational_encyclopedia
            situations = get_situational_encyclopedia().build_commander_situational_prompt(query=query)
            if situations:
                lines.append("\n" + situations)
        except Exception as e:
            logger.debug("Situational encyclopedia not loaded in prompt: %s", e)

        return "\n".join(lines)

    async def get_agent_registry(self) -> Dict[str, dict]:
        """Return a dict for the orchestrator's get_agent_registry() interface."""
        async with self._lock:
            return {
                name: profile.to_dict()
                for name, profile in self._profiles.items()
            }

    # ──────────────────────────────────────────────
    # Introspection
    # ──────────────────────────────────────────────

    async def get_profile(self, name: str) -> Optional[AgentProfile]:
        """Get a single agent's full profile."""
        async with self._lock:
            return self._profiles.get(name)

    async def list_all(self, *, only_available: bool = False) -> List[AgentProfile]:
        """List all registered agents."""
        async with self._lock:
            profiles = list(self._profiles.values())
            if only_available:
                profiles = [p for p in profiles if p.is_available]
            return profiles

    async def get_stats(self) -> Dict[str, Any]:
        """Get registry health metrics."""
        async with self._lock:
            available = sum(1 for p in self._profiles.values() if p.is_available)
            return {
                "total_agents": len(self._profiles),
                "available": available,
                "running": sum(1 for p in self._profiles.values()
                               if p.availability == AgentAvailability.RUNNING),
                "agents": {
                    name: {
                        "availability": p.availability.value,
                        "tasks_completed": p.tasks_completed,
                        "avg_latency_s": round(p.avg_latency_s, 2),
                    }
                    for name, p in self._profiles.items()
                },
            }
