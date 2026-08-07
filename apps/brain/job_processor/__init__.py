# Job Processor - Exactly-once (effectively) job processing engine
"""
This module provides a production-grade job processing system that achieves
"effectively-once" semantics through:

1. Atomic state transitions (PostgreSQL transactions)
2. Idempotency keys (deduplication at submission)
3. Side effect registration (atomic with completion)
4. Lease-based locking (crash recovery)

CRITICAL: "Exactly-once" in the FLP impossibility sense is NOT achievable.
What we guarantee:
- No duplicate side effects from duplicate submissions
- No duplicate side effects from concurrent claims  
- No duplicate side effects from crash+reclaim

What we CANNOT guarantee:
- Partial side effect on crash mid-execution (unavoidable without 2PC)
- Bounded completion time under continuous worker failures
"""

from .idempotency import IdempotencyRegistry
from .job_store import JobState, JobStore
from .worker import Worker

__all__ = ["JobStore", "JobState", "Worker", "IdempotencyRegistry"]
