from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.schemas import PostEbayResponse
from app.config import settings
from app.core.dependencies import get_batch_processor, get_job_store
from app.core.job_models import ItemResult, ItemStatus, JobStatus
from app.core.job_store import JobStore
from app.services.batch_processor import BatchProcessor
from app.services.ebay_poster import EbayPoster
from app.services.storage import generate_presigned_upload

router = APIRouter(prefix="/batch", tags=["batch"])


# --- Schemas ---

class UploadFileItem(BaseModel):
    mime_type: str = "image/jpeg"

class BatchUploadRequest(BaseModel):
    files: list[UploadFileItem] = Field(min_length=1, max_length=5)

class Upload(BaseModel):
    index: int
    presigned_url: str
    s3_key: str

class BatchUploadResponse(BaseModel):
    job_id: str
    uploads: list[Upload]

class BatchImageItem(BaseModel):
    s3_key: str
    mime_type: str = "image/jpeg"

class BatchAnalyzeRequest(BaseModel):
    job_id: str  # ← created by /uploads
    images: list[BatchImageItem] = Field(min_length=1, max_length=5)
    metadata: dict = Field(default_factory=dict)

class BatchAnalyzeResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime

class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: dict
    items: list[ItemResult]
    created_at: datetime
    completed_at: datetime | None


# --- Routes ---

@router.post(
    "/uploads",
    response_model=BatchUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_upload_urls(
    request: BatchUploadRequest,
    job_store: JobStore = Depends(get_job_store),
) -> BatchUploadResponse:
    """
    Step 1: Create a job and generate presigned S3 upload URLs.
    Client uploads images directly to S3, then calls /analyze with s3_keys.
    """
    # 1. Create job
    job = await job_store.create_job(total_items=len(request.files))
    
    # 2. Generate presigned URL for each file
    uploads = []
    for index, file in enumerate(request.files):
        presigned_url, s3_key = await generate_presigned_upload(
            job_id=job.job_id,
            item_index=index,
            mime_type=file.mime_type
        )
        uploads.append(Upload(index=index, presigned_url=presigned_url, s3_key=s3_key))
    
    return BatchUploadResponse(job_id=job.job_id, uploads=uploads)


@router.post(
    "/analyze",
    response_model=BatchAnalyzeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_batch(
    request: BatchAnalyzeRequest,
    background_tasks: BackgroundTasks,
    job_store: JobStore = Depends(get_job_store),
    batch_processor: BatchProcessor = Depends(get_batch_processor),
) -> BatchAnalyzeResponse:
    """
    Step 2: Start processing images already uploaded to S3.
    Poll /batch/jobs/{job_id} for results.
    """
    # 1. Verify job exists
    job = await job_store.get_job(request.job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    
    # 2. Build s3_keys list for processor
    s3_keys = [
        {"s3_key": image.s3_key, "mime_type": image.mime_type}
        for image in request.images
    ]
    
    # 3. Start background processing
    background_tasks.add_task(batch_processor.process_batch, request.job_id, s3_keys)
    
    return BatchAnalyzeResponse(
        job_id=request.job_id,
        status=JobStatus.PROCESSING,
        created_at=job.created_at,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    job_store: JobStore = Depends(get_job_store),
) -> JobStatusResponse:
    """
    Step 3: Poll for job status and results.
    """
    job = await job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    
    return JobStatusResponse(job_id=job_id, status=job.status, progress=job.progress, items=job.items, created_at=job.created_at, completed_at=job.completed_at)


@router.post(
    "/jobs/{job_id}/items/{item_index}/post-ebay",
    response_model=PostEbayResponse,
)
async def post_item_to_ebay(
    job_id: str,
    item_index: int,
    job_store: JobStore = Depends(get_job_store),
) -> PostEbayResponse:
    job = await job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if item_index < 0 or item_index >= len(job.items):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    item = job.items[item_index]
    if item.status != ItemStatus.SUCCESS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Item not ready")

    result = item.result or {}
    listings = result.get("listings", [])
    ebay_listing = next((l for l in listings if l.get("platform") == "ebay"), None)
    if ebay_listing is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No eBay listing found")

    metadata = result.get("metadata", {})
    comps = result.get("comps", {})
    raw_condition = metadata.get("condition")

    listing_data = {
        "title": ebay_listing["title"],
        "description": ebay_listing.get("description", ""),
        "price": comps.get("suggested_price", 0.0),
        "condition": raw_condition.title() if raw_condition else None,
        "brand": metadata.get("brand"),
        "size": metadata.get("size"),
        "color": metadata.get("color"),
        "material": metadata.get("material"),
        "item_type": metadata.get("item_type"),
        "s3_url": result.get("s3_url"),
    }

    poster = EbayPoster(
        user_token=settings.ebay_user_token,
        fulfillment_policy_id=settings.ebay_fulfillment_policy_id,
        payment_policy_id=settings.ebay_payment_policy_id,
        return_policy_id=settings.ebay_return_policy_id,
        merchant_location_key=settings.ebay_merchant_location_key,
    )

    ebay_result = await poster.post_listing(listing_data)

    if ebay_result.success:
        await job_store.update_item(
            job_id,
            item_index,
            ItemStatus.SUCCESS,
            result={
                **result,
                "ebay_listing_id": ebay_result.listing_id,
                "ebay_listing_url": ebay_result.listing_url,
                "ebay_posted_at": datetime.utcnow().isoformat(),
            },
            error=None,
        )
        return PostEbayResponse(
            success=True,
            listing_url=ebay_result.listing_url,
            listing_id=ebay_result.listing_id,
        )

    return PostEbayResponse(success=False, error=ebay_result.error)