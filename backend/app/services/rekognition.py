import boto3
import structlog
from app.config import settings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = structlog.get_logger(__name__)

CONFIDENCE_THRESHOLD = 80.0

CLOTHING_LABELS = {
    "Shirt", "T-Shirt", "Blouse", "Sweater",
    "Hoodie", "Jacket", "Coat", "Pants", "Jeans",
    "Shorts", "Skirt", "Shoe", "Sneaker", "Boot",
    "Sandal", "Footwear", "Clothing", "Apparel",
    "Dress", "Suit", "Hat", "Cap"
}

def _get_rekognition_client():
    """Create Rekognition client from settings."""
    return boto3.client(
        'rekognition',
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
        verify=False
    )

async def is_clothing(image_bytes: bytes, mime_type: str) -> tuple[bool, list[str]]:
    """
    Check if image contains clothing using Rekognition DetectLabels.
    Returns (is_clothing: bool, detected_labels: list[str])
    """
    
    
    try:
        rekognition = _get_rekognition_client()
        
        # Call DetectLabels API
        response = rekognition.detect_labels(
            Image={
                'Bytes': image_bytes  # raw image bytes
            },
            MaxLabels=20,      # how many labels to return? (hint: 20 is enough)
            MinConfidence=CONFIDENCE_THRESHOLD   # our threshold
        )
        detected = [
            label['Name']
            for label in response['Labels']
            # filter: only labels in our CLOTHING_LABELS set
            if label['Name'] in CLOTHING_LABELS
        ]
    
        result = len(detected) > 0
        logger.info(
            "rekognition_complete",
            is_clothing=result,
            detected_clothing_labels=detected
        )
        
        return result, detected
    except Exception as e:
        logger.error("rekognition_error", error=str(e))
        raise
