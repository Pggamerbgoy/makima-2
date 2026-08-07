"""
Worker - Job execution worker with lease management and crash recovery.

The Worker is responsible for:
1. Claiming jobs atomically
2. Checking cancel before executing side effects
3. Executing the side effect
4. Completing the job (atomically registering side effect)
5. Heartbeating to maintain lease during long execution

CRITICAL: The worker checks side_effect_registered BEFORE executing
the side effect. If already registered, another worker completed
this job and we must NOT re-execute.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .job_store import DEFAULT_HEARTBEAT_INTERVAL, JobStore, JobStoreConfig
from .models import Job

logger = logging.getLogger(__name__)


class WorkerConfig:
    """Configuration for Worker."""
    worker_id: str
    job_store_config: JobStoreConfig
    heartbeat_interval: int = DEFAULT_HEARTBEAT_INTERVAL
    poll_interval: float = 1.0  # seconds between claim attempts when idle
    shutdown_timeout: float = 5.0  # seconds to wait for graceful shutdown

    def __init__(
        self,
        dsn: str,
        worker_id: str | None = None,
        heartbeat_interval: int = DEFAULT_HEARTBEAT_INTERVAL,
    ):
        self.job_store_config = JobStoreConfig(dsn=dsn)
        self.worker_id = worker_id or str(uuid.uuid4())
        self.heartbeat_interval = heartbeat_interval


class Worker:
    """
    Job execution worker.
    
    Usage:
        worker = Worker(config)
        await worker.start()
        
        # To stop:
        await worker.stop()
    """

    def __init__(self, config: WorkerConfig):
        self.config = config
        self.job_store: JobStore | None = None
        self._running = False
        self._current_job: Job | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()
        
        # Callbacks
        self._execute_callback: Callable[[Job], Awaitable[Any]] | None = None
        self._error_callback: Callable[[Job, Exception], Awaitable[None]] | None = None

    async def start(self) -> None:
        """Start the worker."""
        self.job_store = JobStore(self.config.job_store_config)
        await self.job_store.initialize()
        self._running = True
        self._shutdown_event.clear()
        logger.info(f"Worker {self.config.worker_id} started")

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        self._running = False
        self._shutdown_event.set()
        
        # Cancel heartbeat task
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # Release current job if any
        if self._current_job:
            logger.warning(f"Stopping with job {self._current_job.job_id} in progress")
        
        await self.job_store.close()
        logger.info(f"Worker {self.config.worker_id} stopped")

    def on_execute(self, callback: Callable[[Job], Awaitable[Any]]) -> None:
        """Register the side effect execution callback."""
        self._execute_callback = callback

    def on_error(self, callback: Callable[[Job, Exception], Awaitable[None]]) -> None:
        """Register error callback."""
        self._error_callback = callback

    async def run_once(self) -> Job | None:
        """
        Claim and execute one job.
        
        Returns the job if one was processed, None if no jobs available.
        This is useful for testing or for running in a loop externally.
        """
        if not self._running:
            return None
        
        # Claim a job
        claim_result = await self.job_store.claim_job(self.config.worker_id)
        
        if not claim_result.success:
            if claim_result.was_already_claimed:
                logger.debug("Job was already claimed by another worker")
            return None
        
        job = claim_result.job
        self._current_job = job
        
        try:
            # Transition to EXECUTING
            success, job = await self.job_store.start_execution(
                job.job_id, self.config.worker_id
            )
            
            if not success:
                logger.warning(f"Failed to start execution for job {job.job_id}")
                return None
            
            # CRITICAL: Check if cancel requested BEFORE executing side effect
            if job.cancel_requested:
                logger.info(f"Job {job.job_id} cancelled before execution")
                await self.job_store.fail_job(
                    job.job_id,
                    self.config.worker_id,
                    "Cancelled before execution",
                    can_retry=False,
                )
                return None
            
            # CRITICAL: Check if side effect already registered
            # This handles the case where another worker completed this job
            # after we claimed it but before we started execution
            if job.side_effect_registered:
                logger.info(f"Job {job.job_id} side effect already registered, skipping")
                return None
            
            # Start heartbeat task for this job
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(job.job_id)
            )
            
            # Execute the side effect
            try:
                if self._execute_callback:
                    result = await self._execute_callback(job)
                else:
                    result = None
                
                # Complete the job (atomically register side effect)
                completion = await self.job_store.complete_job(
                    job.job_id,
                    self.config.worker_id,
                    {"result": result},
                )
                
                if completion.side_effect_was_already_registered:
                    logger.info(
                        f"Job {job.job_id} completed but side effect "
                        f"was already registered by another worker"
                    )
                else:
                    logger.info(f"Job {job.job_id} completed successfully")
                    
            except asyncio.CancelledError:
                # Worker is shutting down
                raise
            except Exception as e:
                # Execution failed
                logger.error(f"Job {job.job_id} execution failed: {e}")
                
                if self._error_callback:
                    await self._error_callback(job, e)
                
                _, will_retry = await self.job_store.fail_job(
                    job.job_id,
                    self.config.worker_id,
                    str(e),
                    can_retry=True,
                )
                
                if will_retry:
                    logger.info(f"Job {job.job_id} will be retried")
                else:
                    logger.error(f"Job {job.job_id} failed permanently")
                    
        finally:
            # Cancel heartbeat task
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
                self._heartbeat_task = None
            
            self._current_job = None
        
        return job

    async def run_loop(self) -> None:
        """
        Run the worker loop, continuously claiming and executing jobs.
        
        Call this as a background task or use start() which calls this.
        """
        while self._running:
            try:
                job = await self.run_once()
                
                if job is None:
                    # No job available, wait before polling again
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.config.poll_interval
                    )
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                await asyncio.sleep(1.0)  # Back off on error

    async def _heartbeat_loop(self, job_id: str) -> None:
        """
        Periodically heartbeat the current job.
        
        This runs as a background task during job execution.
        """
        try:
            while self._running:
                await asyncio.sleep(self.config.heartbeat_interval)
                
                success = await self.job_store.heartbeat(
                    job_id, self.config.worker_id
                )
                
                if not success:
                    logger.warning(
                        f"Heartbeat failed for job {job_id} - "
                        f"job may have been reclaimed"
                    )
                    break
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Heartbeat error for job {job_id}: {e}")

    async def execute_job_direct(
        self, job: Job, side_effect_fn: Callable[[Job], Awaitable[Any]]
    ) -> Any:
        """
        Execute a job directly (for testing).
        
        This bypasses the normal claim flow and is useful for testing
        the completion logic directly.
        """
        result = await side_effect_fn(job)
        
        completion = await self.job_store.complete_job(
            job.job_id,
            self.config.worker_id,
            {"result": result},
        )
        
        return result, completion
