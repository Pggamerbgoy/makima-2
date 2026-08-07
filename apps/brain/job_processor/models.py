"""
Job State Enum and Data Classes
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class JobState(StrEnum):
    """
    Job state machine states.
    
    Valid transitions:
      PENDING → CLAIMED (via claim())
      CLAIMED → EXECUTING (via start_execution())
      CLAIMED → CANCELLED (via cancel())
      EXECUTING → COMPLETED (via complete())
      EXECUTING → FAILED (via fail())
      EXECUTING → CANCELLED (via cancel())
    
    Terminal states: COMPLETED, FAILED, CANCELLED
    """
    PENDING = "pending"
    CLAIMED = "claimed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    """Represents a job in the system."""
    job_id: str
    idempotency_key: str | None
    state: JobState
    payload: dict
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    side_effect_registered: bool = False
    side_effect_result: Any | None = None
    cancel_requested: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error_message: str | None = None
    retry_count: int = 0
    max_retries: int = 3

    def is_terminal(self) -> bool:
        """Check if job is in a terminal state."""
        return self.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED)

    def can_claim(self) -> bool:
        """Check if job can be claimed."""
        return self.state == JobState.PENDING

    def can_execute(self) -> bool:
        """Check if job can execute (claimed by this worker, not cancelled)."""
        return self.state == JobState.CLAIMED and not self.cancel_requested

    def lease_expired(self) -> bool:
        """Check if lease has expired."""
        if self.lease_expires_at is None:
            return False
        return datetime.now(UTC) > self.lease_expires_at


@dataclass
class ClaimResult:
    """Result of a claim attempt."""
    success: bool
    job: Job | None = None
    error: str | None = None
    was_already_claimed: bool = False


@dataclass
class CompletionResult:
    """Result of a completion attempt."""
    success: bool
    side_effect_was_already_registered: bool = False
    error: str | None = None
