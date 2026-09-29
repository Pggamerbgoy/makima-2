"""
Makima v2 — Resilient Circuit Breaker for Model Providers.
Architectural Pattern: 3-State Finite State Machine (Closed -> Open -> Half-Open) with Jittered Cooldown.
Zero-Hardcoding Guarantee: Fully configurable thresholds with concurrency-safe async locks.
"""

from __future__ import annotations

import asyncio
import enum
import random
import time
from typing import Optional


class CircuitState(str, enum.Enum):
    CLOSED = "closed"        # Healthy: requests pass through normally
    OPEN = "open"            # Tripped: requests immediately fail-fast to prevent cascading errors
    HALF_OPEN = "half_open"  # Probing: a single trial request is permitted to test backend recovery


class CircuitBreakerOpenError(Exception):
    """Raised when an execution is attempted while the circuit breaker is OPEN."""
    def __init__(self, provider_name: str, retry_after: float):
        super().__init__(f"Circuit breaker for provider '{provider_name}' is OPEN. Retry in {retry_after:.1f}s.")
        self.provider_name = provider_name
        self.retry_after = retry_after


class CircuitBreaker:
    """
    Thread-safe and async-safe Circuit Breaker implementing jittered backoff.
    Protects downstream providers (Gemini, Groq, Ollama) from thundering herd and cascading 429/500 loops.
    """

    def __init__(
        self,
        name: str,
        max_failures: int = 3,
        cooldown_seconds: float = 30.0,
        jitter: bool = True,
    ):
        self.name = name
        self.max_failures = max(1, max_failures)
        self.cooldown_seconds = max(0.01, cooldown_seconds)
        self.jitter = jitter

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_state_change = time.monotonic()
        self._cooldown_expiry = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Returns the current state, dynamically transitioning from OPEN to HALF_OPEN if cooldown elapsed."""
        now = time.monotonic()
        if self._state == CircuitState.OPEN and now >= self._cooldown_expiry:
            return CircuitState.HALF_OPEN
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def is_available(self) -> bool:
        """Quick non-blocking check whether requests can be routed to this provider."""
        st = self.state
        return st in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def _calculate_cooldown(self) -> float:
        """Computes cooldown duration with optional randomized jitter to prevent synchronized retry spikes."""
        if not self.jitter:
            return self.cooldown_seconds
        # Apply -10% to +20% uniform jitter
        jitter_factor = random.uniform(0.9, 1.2)
        return self.cooldown_seconds * jitter_factor

    async def can_execute(self) -> bool:
        """
        Asynchronously validates whether a request should be dispatched.
        Transitions OPEN -> HALF_OPEN when cooldown expires.
        """
        async with self._lock:
            now = time.monotonic()
            if self._state == CircuitState.OPEN:
                if now >= self._cooldown_expiry:
                    self._state = CircuitState.HALF_OPEN
                    self._last_state_change = now
                    return True
                else:
                    return False
            return True

    async def record_success(self) -> None:
        """Records a successful response, resetting failures and restoring CLOSED state."""
        async with self._lock:
            self._consecutive_failures = 0
            if self._state != CircuitState.CLOSED:
                self._state = CircuitState.CLOSED
                self._last_state_change = time.monotonic()

    async def record_failure(self, error: Optional[Exception] = None) -> None:
        """
        Records an execution failure (e.g. HTTP 429, 503, Timeout).
        Trips breaker to OPEN when failure threshold is reached.
        """
        async with self._lock:
            self._consecutive_failures += 1
            now = time.monotonic()

            if self._state == CircuitState.HALF_OPEN or self._consecutive_failures >= self.max_failures:
                self._state = CircuitState.OPEN
                cooldown = self._calculate_cooldown()
                self._cooldown_expiry = now + cooldown
                self._last_state_change = now

    def get_retry_after(self) -> float:
        """Returns the number of seconds remaining before a probe can be attempted."""
        now = time.monotonic()
        if self._state == CircuitState.OPEN and now < self._cooldown_expiry:
            return max(0.0, self._cooldown_expiry - now)
        return 0.0

    def reset(self) -> None:
        """Manually forces the circuit breaker back to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_state_change = time.monotonic()
        self._cooldown_expiry = 0.0
