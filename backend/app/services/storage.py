import boto3
from botocore.exceptions import ClientError
import structlog
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from app.config import settings

logger = structlog.get_logger(__name__)

MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp"
}

def _get_s3_client():
    """Create S3 client from settings."""
    return boto3.client(
        's3',
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
        verify=False
    )

async def generate_presigned_upload(
    job_id: str,
    item_index: int,
    mime_type: str
) -> tuple[str, str]:
    """
    Generate a presigned URL for direct browser upload to S3.
    Returns (presigned_url, s3_key)
    """
    s3 = _get_s3_client()
    ext = MIME_TO_EXT.get(mime_type, "jpg")
    s3_key = f"uploads/{job_id}/{item_index}.{ext}"  # Build the key using job_id, item_index, ext
    
    presigned_url = s3.generate_presigned_url(
        'put_object',  # PUT because browser is uploading
        Params={
            'Bucket': settings.s3_bucket_name,
            'Key': s3_key,
            'ContentType': mime_type
        },
        ExpiresIn=900  # How many seconds should this URL be valid?
    )
    
    logger.info("presigned_url_generated", job_id=job_id, s3_key=s3_key)
    return presigned_url, s3_key


async def download_image_from_s3(s3_key: str) -> bytes:
    """Download image bytes from S3."""
    s3 = _get_s3_client()
    
    try:
        response = s3.get_object(
            Bucket=settings.s3_bucket_name,
            Key=s3_key
        )
        image_bytes = response['Body'].read()
        logger.info("s3_download_complete", s3_key=s3_key, size=len(image_bytes))
        return image_bytes
    except ClientError as e:
        logger.error("s3_download_failed", s3_key=s3_key, error=str(e))
        raise