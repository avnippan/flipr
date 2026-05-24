import base64
import hashlib
import logging
import structlog
import httpx
from openai import AsyncOpenAI
from openai import APIError, APITimeoutError, RateLimitError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings
from app.models.item import ItemMetadata

logger = structlog.get_logger(__name__)

client = AsyncOpenAI(api_key=settings.openai_api_key, http_client = httpx.AsyncClient(verify=False))

VISION_PROMPT = """
You are an expert thrift reseller and brand authenticator with deep knowledge of secondhand market pricing.

Analyze this image and extract structured item metadata for resale listing generation.

Rules:
- brand: Look carefully for logos, tags, stitching, hardware. Return null if not visible — do not guess.
- item_type: Be specific. "denim jacket" not "jacket". "graphic tee" not "shirt".
- condition: Assess based on visible wear, stains, fading, damage. Return null if condition isn't assessable.
- search_query: This is the most important field. Produce a 3-7 word eBay search string that will match
  actual sold listings. Include brand + item type + distinguishing features.
  GOOD: "Levi's 501 jeans dark wash" / "Nike Air Max 90 white grey" / "Ralph Lauren polo shirt navy"
  BAD: "vintage blue pants" / "nice sneakers" / "men's shirt" (too generic — comps will be worthless)
-- If a graphic appears to be from a known collab, IP, or artist (Disney, Marvel, Keith Haring, 
  Studio Ghibli, etc.), name it specifically in notable_details and search_query.
- notable_details: Be precise about placement. "chest pocket graphic" not "graphic front logo".
  "embroidered patch pocket" not "logo". Specificity here directly improves comp accuracy.
"""


def hash_image(image_bytes: bytes) -> str:
    """SHA-256 hash of image bytes — used as cache key to avoid re-calling vision API."""
    return hashlib.sha256(image_bytes).hexdigest()


@retry(
    retry=retry_if_exception_type((APITimeoutError, RateLimitError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),  # 2s, 4s, 8s... up to 30s
    stop=stop_after_attempt(3),
)
async def analyze_image(image_bytes: bytes, mime_type: str, measurements: dict = None) -> ItemMetadata:
    """
    Send image to vision model and return structured item metadata.
    Retries on timeout and rate limit errors with exponential backoff.
    Raises ValueError on refusal, ValidationError on schema mismatch.
    """
    if settings.use_bedrock:
        from app.services.bedrock_vision import get_bedrock_vision_service
        return await get_bedrock_vision_service().analyze_image(image_bytes, mime_type, measurements)

    measurement_context = ""
    if measurements:
        measurement_context = f"""
        The seller has provided these garment measurements (laid flat):
        - Chest width: {measurements['chest_width_inches']}"
        - Body length: {measurements['body_length_inches']}"

        Use standard sizing charts to infer the size from these measurements.
        Typical men's t-shirt sizing: S=18" chest/27.5" length, M=20"/28.75", L=22"/29.75", XL=24"/30.75".
        If a size label is also visible in the image, prefer that over the measurements.
        """

    prompt = VISION_PROMPT + measurement_context
    image_hash = hash_image(image_bytes)

    log = logger.bind(image_hash=image_hash[:12], mime_type=mime_type)
    log.info("vision_analysis_start")

    # Validate mime type before hitting the API — per NFR: inputs sanitized before external calls
    if mime_type not in settings.allowed_image_types:
        raise ValueError(f"Unsupported image type: {mime_type}. Allowed: {settings.allowed_image_types}")

    b64 = base64.b64encode(image_bytes).decode()

    try:
        response = await client.beta.chat.completions.parse(
            model=settings.openai_vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{b64}",
                                "detail": "high",   # high fidelity — needed for brand logos and tags
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            response_format=ItemMetadata,
            max_tokens=500,             # metadata is compact — cap spend
        )
    except APITimeoutError:
        log.error("vision_analysis_timeout")
        raise
    except RateLimitError:
        log.warning("vision_analysis_rate_limited")
        raise
    except APIError as e:
        log.error(
            "vision_analysis_api_error",
            error=str(e),
            status_code=getattr(e, 'status_code', None)  # ← safe access
        )
        raise

    # .parse() sets refusal when the model declines to process the image
    message = response.choices[0].message
    if message.refusal:
        log.warning("vision_analysis_refused", refusal=message.refusal)
        raise ValueError(f"Model refused to analyze image: {message.refusal}")

    result = message.parsed
    log.info("vision_analysis_complete", item_type=result.item_type, brand=result.brand)

    return result