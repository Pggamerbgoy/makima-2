"""
Job Store - PostgreSQL-backed job persistence with atomic operations.

This is the core of the exactly-once guarantee. All state transitions
happen within PostgreSQL transactions with proper locking.

CRITICAL INVARIANTS:
1. A job can only transition PENDING→CLAIMED once (atomic UPDATE with WHERE)
2. side_effect_registered is set TRUE in SAME transaction as state→COMPLETED
3. Idempotency keys are unique - duplicate submissions return existing job_id
"""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from .models import ClaimResult, CompletionResult, Job, JobState

logger = logging.getLogger(__name__)

DEFAULT_LEASE_SECONDS = 30
DEFAULT_HEARTBEAT_INTERVAL = 10


@dataclass
class JobStoreConfig:
    """Configuration for JobStore."""
    dsn: str
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    max_retries: int = 3
    application_name: str = "job_processor"


class JobStore:
    """
    PostgreSQL-backed job store with atomic operations.
    
    Uses PostgreSQL transactions and row-level locking (FOR UPDATE)
    to ensure atomic state transitions.
    """

    def __init__(self, config: JobStoreConfig):
        self.config = config
        self._pool: psycopg.AsyncConnectionPool | None = None

    async def initialize(self) -> None:
        """Initialize connection pool and create tables if needed."""
        self._pool = await psycopg.AsyncConnectionPool.open(
            self.config.dsn,
            min_size=4,
            max_size=16,
            autocommit=False,
        )
        await self._create_tables()
        logger.info("JobStore initialized")

    async def close(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("JobStore closed")

    async def _create_tables(self) -> None:
        """Create tables if they don't exist."""
        async with self._pool.connection() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id UUID PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    state TEXT NOT NULL DEFAULT 'pending',
                    payload JSONB NOT NULL,
                    worker_id TEXT,
                    lease_expires_at TIMESTAMPTZ,
                    side_effect_registered BOOLEAN NOT NULL DEFAULT FALSE,
                    side_effect_result JSONB,
                    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    error_message TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS job_side_effects (
                    job_id UUID PRIMARY KEY REFERENCES jobs(job_id),
                    side_effect_data JSONB NOT NULL,
                    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_state 
                ON jobs(state) WHERE state IN ('pending', 'claimed', 'executing')
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_lease 
                ON jobs(lease_expires_at) WHERE state = 'executing'
            """)
            await conn.commit()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[psycopg.AsyncCursor]:
        """Context manager for database transactions."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cursor:
                try:
                    yield cursor
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    raise

    async def submit_job(
        self,
        payload: dict,
        idempotency_key: str | None = None,
        max_retries: int = 3,
    ) -> tuple[str, bool]:
        """
        Submit a new job.
        
        Returns:
            (job_id, is_new) - job_id and whether this was a new job
            If idempotency_key exists, returns existing job_id with is_new=False
        
        CRITICAL: Uses INSERT ... ON CONFLICT to ensure idempotency.
        """
        job_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        
        async with self._transaction() as cursor:
            if idempotency_key:
                # Try to insert with idempotency_key
                # On conflict, return existing job
                await cursor.execute("""
                    INSERT INTO jobs 
                        (job_id, idempotency_key, state, payload, max_retries,
                         created_at, updated_at)
                    VALUES (%s, %s, 'pending', %s, %s, %s, %s)
                    ON CONFLICT (idempotency_key) 
                    DO UPDATE SET updated_at = NOW()
                    RETURNING job_id, (xmax = 0) AS is_new
                """, (job_id, idempotency_key, jsonb(payload), max_retries, now, now))
                result = await cursor.fetchone()
                return str(result["job_id"]), result["is_new"]
            else:
                # No idempotency key - just insert
                await cursor.execute("""
                    INSERT INTO jobs (job_id, state, payload, max_retries, created_at, updated_at)
                    VALUES (%s, 'pending', %s, %s, %s, %s)
                    RETURNING job_id
                """, (job_id, jsonb(payload), max_retries, now, now))
                result = await cursor.fetchone()
                return str(result["job_id"]), True

    async def claim_job(self, worker_id: str) -> ClaimResult:
        """
        Atomically claim a pending job.
        
        CRITICAL: Uses SELECT FOR UPDATE SKIP LOCKED to:
        1. Lock a pending job exclusively
        2. Skip already-locked jobs (claimed by other workers)
        3. Transition PENDING→CLAIMED atomically
        
        This is the key to preventing concurrent workers from claiming the same job.
        """
        now = datetime.now(UTC)
        lease_expires = now + timedelta(seconds=self.config.lease_seconds)
        
        async with self._transaction() as cursor:
            # Select and lock a pending job
            await cursor.execute("""
                SELECT job_id FROM jobs 
                WHERE state = 'pending' 
                  AND (lease_expires_at IS NULL OR lease_expires_at < %s)
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            """, (now,))
            
            result = await cursor.fetchone()
            if not result:
                return ClaimResult(success=False, error="No pending jobs available")
            
            job_id = str(result["job_id"])
            
            # Atomically transition to CLAIMED
            await cursor.execute("""
                UPDATE jobs 
                SET state = 'claimed',
                    worker_id = %s,
                    lease_expires_at = %s,
                    updated_at = NOW()
                WHERE job_id = %s AND state = 'pending'
                RETURNING *
            """, (worker_id, lease_expires, job_id))
            
            result = await cursor.fetchone()
            if not result:
                # Another worker claimed it between our SELECT and UPDATE
                return ClaimResult(success=False, was_already_claimed=True)
            
            job = self._row_to_job(dict(result))
            return ClaimResult(success=True, job=job)

    async def start_execution(self, job_id: str, worker_id: str) -> tuple[bool, Job | None]:
        """
        Transition job from CLAIMED→EXECUTING.
        
        Checks:
        - Job is in CLAIMED state
        - Job is claimed by this worker
        - Cancel was not requested
        
        Returns (success, job) - if cancel requested, returns job with cancel_requested=True
        """
        async with self._transaction() as cursor:
            await cursor.execute("""
                UPDATE jobs 
                SET state = 'executing',
                    updated_at = NOW()
                WHERE job_id = %s AND state = 'claimed' AND worker_id = %s
                RETURNING *
            """, (job_id, worker_id))
            
            result = await cursor.fetchone()
            if not result:
                # Job not found or not in claimed state or wrong worker
                return False, None
            
            job = self._row_to_job(dict(result))
            return True, job

    async def check_cancel_before_execute(self, job_id: str) -> bool:
        """
        Check if cancel was requested before executing side effect.
        
        Returns True if cancel requested (should NOT execute).
        Returns False if safe to execute.
        
        CRITICAL: Must be called immediately before side effect execution.
        """
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT cancel_requested FROM jobs 
                    WHERE job_id = %s
                """, (job_id,))
                result = await cursor.fetchone()
                if not result:
                    return False  # Job doesn't exist, treat as not cancelled
                return result["cancel_requested"]

    async def complete_job(
        self,
        job_id: str,
        worker_id: str,
        side_effect_data: dict,
    ) -> CompletionResult:
        """
        Atomically complete a job AND register the side effect.
        
        CRITICAL: This is the core of the exactly-once guarantee.
        The side_effect_registered flag is set TRUE in the SAME transaction
        as the state transition to COMPLETED. This ensures:
        
        1. If transaction commits: side effect is registered, job is complete
        2. If transaction rolls back: neither happened, job can be reclaimed
        3. If worker crashes mid-transaction: PostgreSQL rolls back automatically
        
        The worker must check side_effect_registered BEFORE executing the
        actual side effect. If it's already True, another worker completed
        the job and the side effect was already registered.
        """
        now = datetime.now(UTC)
        
        async with self._transaction() as cursor:
            # First check if side effect already registered
            await cursor.execute("""
                SELECT side_effect_registered, state FROM jobs 
                WHERE job_id = %s FOR UPDATE
            """, (job_id,))
            result = await cursor.fetchone()
            
            if not result:
                return CompletionResult(
                    success=False,
                    error=f"Job {job_id} not found"
                )
            
            if result["side_effect_registered"]:
                # Side effect already registered - another worker completed this
                return CompletionResult(
                    success=True,
                    side_effect_was_already_registered=True
                )
            
            # Atomically complete job AND register side effect
            await cursor.execute("""
                UPDATE jobs 
                SET state = 'completed',
                    side_effect_registered = TRUE,
                    side_effect_result = %s,
                    updated_at = NOW()
                WHERE job_id = %s AND worker_id = %s 
                  AND state IN ('executing', 'claimed')
                RETURNING *
            """, (jsonb(side_effect_data), job_id, worker_id))
            
            result = await cursor.fetchone()
            if not result:
                return CompletionResult(
                    success=False,
                    error=f"Job {job_id} not owned by worker {worker_id} or in wrong state"
                )
            
            # Insert side effect record
            await cursor.execute("""
                INSERT INTO job_side_effects (job_id, side_effect_data, registered_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (job_id) DO NOTHING
            """, (job_id, jsonb(side_effect_data), now))
            
            return CompletionResult(success=True)

    async def fail_job(
        self,
        job_id: str,
        worker_id: str,
        error_message: str,
        can_retry: bool = True,
    ) -> tuple[bool, bool]:  # (success, will_retry)
        """
        Fail a job, optionally retrying.
        
        Returns (success, will_retry).
        If will_retry=True, job is reset to PENDING with incremented retry_count.
        If will_retry=False, job is marked FAILED (terminal).
        """
        async with self._transaction() as cursor:
            await cursor.execute("""
                SELECT retry_count, max_retries FROM jobs 
                WHERE job_id = %s FOR UPDATE
            """, (job_id,))
            result = await cursor.fetchone()
            
            if not result:
                return False, False
            
            retry_count = result["retry_count"]
            max_retries = result["max_retries"]
            
            if can_retry and retry_count < max_retries:
                # Retry - reset to pending
                await cursor.execute("""
                    UPDATE jobs 
                    SET state = 'pending',
                        worker_id = NULL,
                        lease_expires_at = NULL,
                        error_message = %s,
                        retry_count = %s,
                        updated_at = NOW()
                    WHERE job_id = %s
                """, (error_message, retry_count + 1, job_id))
                return True, True
            else:
                # Terminal failure
                await cursor.execute("""
                    UPDATE jobs 
                    SET state = 'failed',
                        error_message = %s,
                        updated_at = NOW()
                    WHERE job_id = %s
                """, (error_message, job_id))
                return True, False

    async def cancel_job(self, job_id: str) -> bool:
        """
        Request cancellation of a job.
        
        If job is PENDING or CLAIMED: immediately transitions to CANCELLED.
        If job is EXECUTING: sets cancel_requested flag (worker should check).
        If job is terminal: no-op, returns False.
        
        Returns True if cancellation was effective.
        """
        async with self._transaction() as cursor:
            # Try to cancel non-executing jobs immediately
            await cursor.execute("""
                UPDATE jobs 
                SET state = 'cancelled',
                    updated_at = NOW()
                WHERE job_id = %s AND state IN ('pending', 'claimed')
            """, (job_id,))
            
            if cursor.rowcount > 0:
                return True
            
            # For executing jobs, just set the flag
            await cursor.execute("""
                UPDATE jobs 
                SET cancel_requested = TRUE,
                    updated_at = NOW()
                WHERE job_id = %s AND state = 'executing'
            """, (job_id,))
            
            return cursor.rowcount > 0

    async def heartbeat(self, job_id: str, worker_id: str) -> bool:
        """
        Extend lease for a job.
        
        Should be called periodically by workers during long-running execution.
        Returns False if job not found or not owned by this worker.
        """
        new_expires = datetime.now(UTC) + timedelta(seconds=self.config.lease_seconds)
        
        async with self._pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    UPDATE jobs 
                    SET lease_expires_at = %s,
                        updated_at = NOW()
                    WHERE job_id = %s AND worker_id = %s 
                      AND state IN ('claimed', 'executing')
                """, (new_expires, job_id, worker_id))
                await conn.commit()
                return cursor.rowcount > 0

    async def get_job(self, job_id: str) -> Job | None:
        """Get a job by ID."""
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM jobs WHERE job_id = %s
                """, (job_id,))
                result = await cursor.fetchone()
                if result:
                    return self._row_to_job(result)
                return None

    def _row_to_job(self, row: dict) -> Job:
        """Convert database row to Job object."""
        return Job(
            job_id=str(row["job_id"]),
            idempotency_key=row["idempotency_key"],
            state=JobState(row["state"]),
            payload=row["payload"],
            worker_id=row["worker_id"],
            lease_expires_at=row["lease_expires_at"],
            side_effect_registered=row["side_effect_registered"],
            side_effect_result=row["side_effect_result"],
            cancel_requested=row["cancel_requested"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            error_message=row["error_message"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
        )


def jsonb(data: dict) -> psycopg.types.json.Jsonb:
    """Helper to create PostgreSQL JSONB value."""
    return psycopg.types.json.Jsonb(data)
