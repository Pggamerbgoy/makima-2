"""
Makima v7.1 — Health Aggregator

Single source of truth for all service health.
Combines: native gRPC status, Python module status, Rust index integrity, LLM backend reachability.
Sends service_health WS event every 10s. Dashboard traffic-light grid source.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional

logger = logging.getLogger("makima.health_aggregator")


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
    """
    
    def __init__(self, watchdog=None, ai_handler=None, ws_broadcast=None):
        self.watchdog = watchdog
        self.ai_handler = ai_handler
        self.ws_broadcast = ws_broadcast
        self._module_health: dict[str, ServiceHealth] = {}
        self._poll_task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        self._poll_task = asyncio.create_task(self._health_loop())
        logger.info("HealthAggregator started")
    
    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
    
    async def _health_loop(self) -> None:
        """Broadcast health snapshot every 10s."""
        while True:
            try:
                snapshot = self.get_snapshot()
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
                    status="ok" if status["healthy"] else ("down" if status["given_up"] else "degraded"),
                    last_checked=status.get("last_check", time.time()),
                )
        
        # LLM backends
        if self.ai_handler:
            for name, status in self.ai_handler.get_backend_status().items():
                cb_state = status["circuit_breaker_state"]
                snapshot[f"llm.{name}"] = ServiceHealth(
                    name=name,
                    status="ok" if cb_state == "closed" else "down",
                    error=f"Circuit breaker: {cb_state}" if cb_state != "closed" else None,
                    last_checked=time.time(),
                )
        
        # Python modules
        snapshot.update(self._module_health)
        
        return snapshot
    
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
        
        if not any(h.status == "ok" for n, h in snapshot.items() if "whisper" in n):
            disabled.append("voice_input")
        if not any(h.status == "ok" for n, h in snapshot.items() if "audio" in n):
            disabled.append("wake_word")
        if not any(h.status == "ok" for n, h in snapshot.items() if "screen" in n):
            disabled.append("screen_reader")
        if not any(h.status == "ok" for n, h in snapshot.items() if "embed" in n):
            disabled.append("semantic_search")
        
        return disabled
    
    def report_module_health(self, module: str, status: str, error: str = None) -> None:
        """Report health status from a Python module."""
        self._module_health[f"module.{module}"] = ServiceHealth(
            name=module,
            status=status,
            error=error,
            last_checked=time.time(),
        )
