"""
Idempotency Registry - Deduplication for job submissions.

This ensures that duplicate submissions with the same idempotency_key
return the same job_id without creating duplicate jobs or executing
side effects multiple times.

CRITICAL: Uses PostgreSQL's INSERT ... ON CONFLICT DO NOTHING
to ensure atomic check-and-insert.
"""

import logging

from .job_store import JobStore

logger = logging.getLogger(__name__)


class IdempotencyRegistry:
    """
    Registry for idempotency keys.
    
    This is a thin wrapper around JobStore's idempotency functionality,
    providing a cleaner API for the submission layer.
    """

    def __init__(self, job_store: JobStore):
        self.job_store = job_store

    async def get_or_create(
        self,
        idempotency_key: str,
        payload: dict,
        max_retries: int = 3,
    ) -> tuple[str, bool]:
        """
        Get existing job or create new one for idempotency key.
        
        Returns:
            (job_id, is_new) - job_id and whether this was a new job
        
        If idempotency_key already exists:
            - Returns existing job_id
            - is_new = False
            - Does NOT re-execute side effect
        
        If idempotency_key is new:
            - Creates new job in PENDING state
            - Returns new job_id
            - is_new = True
        """
        return await self.job_store.submit_job(
            payload=payload,
            idempotency_key=idempotency_key,
            max_retries=max_retries,
        )

    async def check_idempotency(
        self,
        idempotency_key: str,
    ) -> str | None:
        """
        Check if an idempotency key exists and return the job_id.
        
        Returns None if key doesn't exist.
        """
        # Query the jobs table directly for the idempotency key
        async with self.job_store._pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT job_id FROM jobs 
                    WHERE idempotency_key = %s
                """, (idempotency_key,))
                result = await cursor.fetchone()
                if result:
                    return str(result[0])
                return None
