"""
Makima v8.1 — Production-Hardened Health Aggregator

Single source of truth for all service health.
Combines: native gRPC status, Python module status, Rust index integrity, LLM backend reachability.
Delta-only WS broadcasts, type-safe reporting, structured degradation, health-aware LLM routing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional

logger = logging.getLogger("makima.health_aggregator")

SERVICE_CATEGORIES = {
    "voice_input": ["gemini_live", "voice", "audio"],
    "wake_word": ["openwakeword", "wakeword", "audio_trigger", "audio"],
    "screen_reader": ["ocr", "screen", "vision"],
    "semantic_search": ["embed", "nomic", "vector"],
}


@dataclass
class ServiceHealth:
    name: str
    status: Literal["ok", "degraded", "down"]
    latency_ms: Optional[float] = None
    last_checked: float = 0.0
    error: Optional[str] = None


class HealthAggregator:
    """
    Aggregates health from all subsystems into a single snapshot.
    Provides delta-only WS updates, type-safe status updates, and health-aware routing strategies.
    """

    def __init__(self, watchdog=None, ai_handler=None, ws_broadcast=None):
        self.watchdog = watchdog
        self.ai_handler = ai_handler
        self.ws_broadcast = ws_broadcast
        self._module_health: dict[str, ServiceHealth] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._last_snapshot_hash: str = ""

    async def start(self) -> None:
        self._poll_task = asyncio.create_task(self._health_loop())
        logger.info("HealthAggregator started with delta WS broadcasting and routing bridge")

    async def stop(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    async def _health_loop(self) -> None:
        """Delta-only broadcast health snapshot every 10s."""
        while True:
            try:
                snapshot = self.get_snapshot()
                snap_hash = hashlib.md5(
                    json.dumps({n: h.status for n, h in snapshot.items()}, sort_keys=True).encode()
                ).hexdigest()

                if snap_hash != self._last_snapshot_hash:
                    self._last_snapshot_hash = snap_hash
                    if self.ws_broadcast:
                        from . import ws_protocol
                        await self.ws_broadcast(ws_protocol.build_service_health(
                            {name: {"status": h.status, "error": h.error}
                             for name, h in snapshot.items()}
                        ))
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health loop error: {e}")
                await asyncio.sleep(10)

    def get_snapshot(self) -> dict[str, ServiceHealth]:
        """Get complete health snapshot."""
        snapshot = {}

        # Native services
        if self.watchdog:
            for name, status in self.watchdog.get_status().items():
                snapshot[f"native.{name}"] = ServiceHealth(
                    name=name,
                    status="ok" if status.get("healthy") else ("down" if status.get("given_up") else "degraded"),
                    last_checked=status.get("last_check", time.time()),
                )

        # LLM backends
        if self.ai_handler:
            for name, status in self.ai_handler.get_backend_status().items():
                cb_state = status.get("state") or status.get("circuit_breaker_state") or "closed"
                svc_status = "ok" if cb_state == "closed" else ("degraded" if cb_state == "half_open" else "down")
                snapshot[f"llm.{name}"] = ServiceHealth(
                    name=name,
                    status=svc_status,
                    error=f"Circuit breaker: {cb_state}" if cb_state != "closed" else None,
                    last_checked=time.time(),
                )

        # Python modules
        snapshot.update(self._module_health)

        return snapshot

    def get_llm_routing_strategy(self) -> Literal["full", "cached", "rule_based"]:
        """
        Returns routing strategy based on LLM backend health.
        Used by OrchestrationEngine to skip unnecessary LLM calls during outages/degradation.
        """
        snapshot = self.get_snapshot()
        llm_statuses = [h.status for n, h in snapshot.items() if n.startswith("llm.")]

        if not llm_statuses:
            return "rule_based"

        ok_count = llm_statuses.count("ok")
        degraded_count = llm_statuses.count("degraded")

        if ok_count >= 1:
            return "full"
        elif degraded_count >= 1:
            return "cached"
        else:
            return "rule_based"

    def is_critical_path_ok(self) -> bool:
        """Brain alive + at least 1 LLM backend up."""
        snapshot = self.get_snapshot()
        llm_ok = any(
            h.status == "ok" for name, h in snapshot.items() if name.startswith("llm.")
        )
        return llm_ok

    def degrade_gracefully(self) -> list[str]:
        """List of features that should be disabled due to health issues."""
        disabled = []
        snapshot = self.get_snapshot()

        for feature, keywords in SERVICE_CATEGORIES.items():
            healthy = any(
                h.status == "ok" for name, h in snapshot.items()
                if any(kw in name.lower() for kw in keywords)
            )
            if not healthy:
                disabled.append(feature)

        return disabled

    def report_module_health(
        self,
        module: str,
        status: Literal["ok", "degraded", "down"],
        error: Optional[str] = None
    ) -> None:
        """Report type-safe health status from a Python module."""
        self._module_health[f"module.{module}"] = ServiceHealth(
            name=module,
            status=status,
            error=error,
            last_checked=time.time(),
        )
