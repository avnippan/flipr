import asyncio
from abc import ABC, abstractmethod
from app.core.job_models import ItemResult
from datetime import datetime

import structlog

from app.core.job_models import ItemResult, Job, JobStatus, ItemStatus

logger = structlog.get_logger(__name__)


class JobStore(ABC):
    """Abstract interface for job persistence. Swap implementations without changing callers."""

    @abstractmethod
    async def create_job(self, total_items: int, metadata: dict | None = None) -> Job:
        """Create a new job with `total_items` PENDING ItemResults pre-populated."""

    @abstractmethod
    async def get_job(self, job_id: str) -> Job | None:
        """Return the Job for `job_id`, or None if it does not exist."""

    @abstractmethod
    async def update_job(
        self,
        job_id: str,
        status: JobStatus | None = None,
        items: list[ItemResult] | None = None,
    ) -> Job:
        """Overwrite top-level job fields. Raises ValueError if job not found."""

    @abstractmethod
    async def update_item(
        self,
        job_id: str,
        item_index: int,
        status: ItemStatus,
        result: dict | None = None,
        error: str | None = None,
    ) -> Job:
        """Update a single item result and recompute overall job status. Raises ValueError if job not found."""


class InMemoryJobStore(JobStore):
    """In-process job store backed by a plain dict. Suitable for single-process deployments."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()

    async def create_job(self, total_items: int, metadata: dict | None = None) -> Job:
        """Create a new job and pre-populate one PENDING ItemResult per item."""
        job = Job(
            total_items=total_items,
            metadata=metadata or {},
            items=[
                ItemResult(index=i, status=ItemStatus.PENDING)
                for i in range(total_items)
            ],
        )
        async with self._lock:
            self._jobs[job.job_id] = job

        logger.info("job_created", job_id=job.job_id, total_items=total_items)
        return job

    async def get_job(self, job_id: str) -> Job | None:
        """Return the job or None — does not raise."""
        async with self._lock:
            return self._jobs.get(job_id)

    async def update_job(
        self,
        job_id: str,
        status: JobStatus | None = None,
        items: list[ItemResult] | None = None,
    ) -> Job:
        """Patch top-level job fields. Raises ValueError if the job does not exist."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError(f"Job not found: {job_id}")

            update: dict = {}
            if status is not None:
                update["status"] = status
                if status == JobStatus.COMPLETED:
                    update["completed_at"] = datetime.utcnow()
            if items is not None:
                update["items"] = items

            job = job.model_copy(update=update)
            self._jobs[job_id] = job

        logger.info("job_updated", job_id=job_id, status=status)
        return job

    async def update_item(
        self,
        job_id: str,
        item_index: int,
        status: ItemStatus,
        result: dict | None = None,
        error: str | None = None,
    ) -> Job:
        """Update one item and derive the new overall job status. Raises ValueError if the job does not exist."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError(f"Job not found: {job_id}")

            items = list[ItemResult](job.items)
            items[item_index] = items[item_index].model_copy(
                update={
                    "status": status,
                    "result": result,
                    "error": error,
                    "completed_at": datetime.utcnow() if status in (ItemStatus.SUCCESS, ItemStatus.FAILED) else None,
                }
            )

            terminal = {ItemStatus.SUCCESS, ItemStatus.FAILED}
            all_done = all(item.status in terminal for item in items)
            any_processing = any(item.status == ItemStatus.PROCESSING for item in items)

            if all_done:
                job_status = JobStatus.COMPLETED
                completed_at = datetime.utcnow()
            elif any_processing:
                job_status = JobStatus.PROCESSING
                completed_at = None
            else:
                job_status = job.status
                completed_at = job.completed_at

            job = job.model_copy(
                update={
                    "items": items,
                    "status": job_status,
                    "completed_at": completed_at,
                }
            )
            self._jobs[job_id] = job

        logger.info(
            "item_updated",
            job_id=job_id,
            item_index=item_index,
            item_status=status,
            job_status=job_status,
        )
        return job
