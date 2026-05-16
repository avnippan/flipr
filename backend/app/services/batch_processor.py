import asyncio

import structlog

from app.core.job_models import JobStatus, ItemStatus
from app.core.job_store import JobStore
from app.services.listing import draft_listings
from app.services.pricing import fetch_sold_comps
from app.services.vision import analyze_image
from app.services.storage import download_image_from_s3
from app.services.rekognition import is_clothing

logger = structlog.get_logger(__name__)

_RETRYABLE_SIGNALS = ("timeout", "rate limit", "429", "503", "connection")


class BatchProcessor:
    def __init__(self, job_store: JobStore) -> None:
        self._store = job_store

    async def process_batch(self, job_id: str, s3_keys: list[dict]) -> None:
        """Run all images concurrently and update job state as items complete."""

        logger.info("process_batch_start", job_id=job_id)
        await self._store.update_job(job_id, status=JobStatus.PROCESSING)
        logger.info("process_batch_job_updated", job_id=job_id)

        tasks = [
            self._process_single_item(job_id, index, item["s3_key"], item["mime_type"])
            for index, item in enumerate(s3_keys)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("batch_complete", job_id=job_id, total=len(s3_keys))

    async def _process_single_item(self, job_id, index, s3_key, mime_type):
        """Run the full analysis pipeline for one image and persist the outcome."""

        log = logger.bind(job_id=job_id, index=index)
        log.info("process_single_item_start")
        
        try:
            await self._store.update_item(job_id, index, ItemStatus.PROCESSING)
            log.info("item_marked_processing")

            # Step 1: Download from S3
            image_bytes = await download_image_from_s3(s3_key)
            
            # Step 2: Rekognition pre-filter
            clothing_detected, labels = await is_clothing(image_bytes, mime_type)
            if not clothing_detected:
                raise ValueError(f"No clothing detected. Found labels: {labels}")

            metadata = await analyze_image(image_bytes, mime_type)
            comps = await fetch_sold_comps(metadata.search_query)
            listings = await draft_listings(metadata, comps)

            result = {
                "metadata": metadata.model_dump(),
                "comps": comps.model_dump(),
                "listings": [listing.model_dump() for listing in listings],
            }

            await self._store.update_item(
                job_id, index, ItemStatus.SUCCESS, result=result
            )
            log.info("item_complete")

        except Exception as exc:
            retryable = self._is_retryable_error(exc)
            log.error(
                "item_failed",
                error=str(exc),
                retryable=retryable,
            )
            await self._store.update_item(
                job_id, index, ItemStatus.FAILED, error=str(exc)
            )

    def _is_retryable_error(self, error: Exception) -> bool:
        """Signal to future retry logic whether the error is transient."""
        msg = str(error).lower()
        return any(signal in msg for signal in _RETRYABLE_SIGNALS)
