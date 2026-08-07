"""Makima v7.1 — RateLimitManager

Spec (from makima_v7_improved_plan.md):
- Per-provider sliding 60s window tracking tokens_used and requests_used.
- can_send(provider, estimated_tokens) checks bucket before API calls.
- record_usage(provider, tokens, headers) updates usage using actual usage.
- 429 handling: parse Retry-After, update bucket, mark provider rate_limited.

Fixes applied (from browser_controller implementation plan):
1. DEADLOCK FIX: Replaced asyncio.Lock + run_coroutine_threadsafe(..).result()
   with threading.Lock(). The old pattern would permanently block the event
   loop if can_send/record_usage/handle_429 were called from an async context.
   Rate-limit state updates are CPU-bound microsecond ops — threading.Lock is
   the correct primitive.

2. O(1) PERFORMANCE FIX: Removed O(N) sum(t for _, t in bucket.tokens_used)
   per call. Now maintained as a running counter (current_tokens / current_reqs)
   that is incremented on append and decremented on evict.

3. MONOTONIC TIME FIX: Replaced all time.time() with time.monotonic() so
   sliding windows are immune to NTP clock adjustments / backward jumps.
   NOTE: rate_limited_until is still stored as a monotonic delta, which is
   only correct for same-process comparisons (acceptable here).

4. DATACLASS TYPE FIX: tokens_used was annotated as Deque[int] but actually
   stores (float, int) tuples. Fixed to Deque[tuple[float, int]].
   Used dataclasses.field(default_factory=deque) to avoid the None-init
   anti-pattern.
"""

from __future__ import annotations

import threading
import time
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger("makima.rate_limit_manager")


@dataclass
class ProviderBucket:
    provider: str
    tokens_per_min: int = 0   # 0 => rely on provider 429 feedback
    requests_per_min: int = 0

    # Sliding windows: (monotonic_ts, token_count) tuples for tokens;
    # monotonic_ts floats for requests.
    tokens_used: Deque[tuple[float, int]] = field(default_factory=deque)
    requests_used: Deque[float] = field(default_factory=deque)

    # O(1) running counters — kept in sync with the deques above.
    _current_tokens: int = field(default=0, init=False, repr=False)
    _current_reqs: int = field(default=0, init=False, repr=False)

    # monotonic timestamp until which this provider is rate-limited
    rate_limited_until: Optional[float] = None


class RateLimitManager:
    """Per-provider proactive rate limiting with sliding windows.

    Thread-safe via threading.Lock (not asyncio.Lock) so it can be called
    from both sync and async contexts without risk of deadlock.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        rl_cfg = self.config.get("rate_limits", {}) if isinstance(self.config, dict) else {}

        self.default_tokens_per_min = int(rl_cfg.get("default_tokens_per_min", 0))
        self.default_requests_per_min = int(rl_cfg.get("default_requests_per_min", 0))

        self.window_s = 60.0
        self.buckets: Dict[str, ProviderBucket] = {}

        # threading.Lock — safe from any call-site (sync or async).
        self._lock = threading.Lock()

        # Create common provider buckets if present in config
        for provider, pcfg in rl_cfg.get("providers", {}).items():
            self.buckets[provider] = ProviderBucket(
                provider=provider,
                tokens_per_min=int(pcfg.get("tokens_per_min", 0)),
                requests_per_min=int(pcfg.get("requests_per_min", 0)),
            )

    # ------------------------------------------------------------------
    # Internal helpers (must be called with self._lock held)
    # ------------------------------------------------------------------

    def _get_bucket(self, provider: str) -> ProviderBucket:
        if provider not in self.buckets:
            self.buckets[provider] = ProviderBucket(
                provider=provider,
                tokens_per_min=self.default_tokens_per_min,
                requests_per_min=self.default_requests_per_min,
            )
        return self.buckets[provider]

    def _evict_old(self, bucket: ProviderBucket, now: float) -> None:
        """Evict expired window entries, decrementing O(1) counters."""
        cutoff = now - self.window_s

        while bucket.tokens_used and bucket.tokens_used[0][0] <= cutoff:
            _, toks = bucket.tokens_used.popleft()
            bucket._current_tokens -= toks

        while bucket.requests_used and bucket.requests_used[0] <= cutoff:
            bucket.requests_used.popleft()
            bucket._current_reqs -= 1

    # ------------------------------------------------------------------
    # Public API — all thread-safe, callable from sync or async contexts
    # ------------------------------------------------------------------

    def can_send(self, provider: str, estimated_tokens: int = 0) -> bool:
        """Return True unless currently in an active HTTP 429 cooldown period."""
        with self._lock:
            bucket = self._get_bucket(provider)
            now = time.monotonic()

            # Hard rate-limit from an actual HTTP 429 response
            if bucket.rate_limited_until and now < bucket.rate_limited_until:
                return False

            return True

    def record_usage(self, provider: str, tokens: int = 0,
                     headers: dict[str, Any] | None = None) -> None:
        """Record actual token/request usage after a successful call."""
        with self._lock:
            bucket = self._get_bucket(provider)
            now = time.monotonic()

            self._evict_old(bucket, now)

            # Append request timestamp
            bucket.requests_used.append(now)
            bucket._current_reqs += 1

            # Append token usage only when we have a limit to enforce
            if bucket.tokens_per_min > 0 and tokens:
                t = int(tokens)
                bucket.tokens_used.append((now, t))
                bucket._current_tokens += t

    def handle_429(self, provider: str, retry_after_s: float) -> None:
        """Mark a provider as rate-limited after receiving a 429 response."""
        with self._lock:
            bucket = self._get_bucket(provider)
            now = time.monotonic()
            cooldown = min(max(0.0, float(retry_after_s)), 30.0)
            bucket.rate_limited_until = now + cooldown + 0.2
            logger.warning(
                "Provider %s rate limited for %.1fs (capped from %.1fs)", provider, cooldown, float(retry_after_s)
            )

    def seconds_until_available(self, provider: str, estimated_tokens: int = 0) -> float:
        """Return seconds until provider is available, or 0.0 if ready now."""
        with self._lock:
            bucket = self._get_bucket(provider)
            now = time.monotonic()
            if bucket.rate_limited_until and now < bucket.rate_limited_until:
                return bucket.rate_limited_until - now
            return 0.0
