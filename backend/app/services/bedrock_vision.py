import asyncio
import json
import structlog
import boto3
from botocore.exceptions import ClientError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings
from app.models.item import ItemMetadata
from app.services.vision import VISION_PROMPT

logger = structlog.get_logger(__name__)

_JSON_SCHEMA_HINT = """
Return ONLY a JSON object with exactly these fields (no markdown, no extra text):
{
  "brand": null or "string",
  "item_type": "string (required, e.g. 'denim jacket', 'graphic tee')",
  "color": null or "string",
  "size": null or "string",
  "condition": null or "poor" or "fair" or "good" or "excellent",
  "material": null or "string",
  "notable_details": ["list", "of", "strings"],
  "search_query": "string (required, e.g. 'Levi's 501 jeans dark wash')"
}
"""

_MIME_TO_FORMAT = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _strip_json_markdown(text: str) -> str:
    """Remove markdown code blocks from JSON response."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


class BedrockVisionService:
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=settings.aws_region,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                verify=False,
            )
        return self._client

    @retry(
        retry=retry_if_exception_type(ClientError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
    )
    def _call_converse(self, image_bytes: bytes, image_format: str, prompt: str) -> str:
        """Call Bedrock Converse API with image and prompt."""
        try:
            converse_kwargs = {
                "modelId": settings.bedrock_model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
                            {"text": prompt},
                        ],
                    }
                ],
                "inferenceConfig": {
                    "temperature": 0.3,
                    "maxTokens": 500,
                },
            }
            if settings.bedrock_guardrail_id:
                converse_kwargs["guardrailConfig"] = {
                    "guardrailIdentifier": settings.bedrock_guardrail_id,
                    "guardrailVersion": settings.bedrock_guardrail_version,
                    "trace": "enabled",
                }
            response = self.client.converse(**converse_kwargs)
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = str(e)
            
            if "guardrail" in error_msg.lower():
                logger.warning("bedrock_vision_guardrail_block", error=error_msg)
                raise ValueError("Image contains inappropriate content and cannot be processed.")
            
            logger.error(
                "bedrock_vision_client_error",
                code=error_code,
                error=error_msg,
            )
            raise

        return response["output"]["message"]["content"][0]["text"]

    async def analyze_image(
        self, image_bytes: bytes, mime_type: str, measurements: dict = None
    ) -> ItemMetadata:
        """Analyze image using Bedrock Converse API. Returns ItemMetadata."""
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

        prompt = VISION_PROMPT + measurement_context + _JSON_SCHEMA_HINT
        image_format = _MIME_TO_FORMAT.get(mime_type, "jpeg")

        log = logger.bind(mime_type=mime_type)
        log.info("bedrock_vision_start")

        try:
            raw_text = await asyncio.to_thread(
                self._call_converse, image_bytes, image_format, prompt
            )
            
            # Check for guardrail block message BEFORE trying to parse JSON
            if "content policy restrictions" in raw_text.lower():
                log.warning("bedrock_vision_guardrail_blocked", response=raw_text)
                raise ValueError(raw_text)
            
            clean_json = _strip_json_markdown(raw_text)
            result = ItemMetadata(**json.loads(clean_json))
            
            log.info(
                "bedrock_vision_complete",
                item_type=result.item_type,
                brand=result.brand,
            )
            return result
    
        except json.JSONDecodeError as e:
            log.error("bedrock_vision_json_parse_error", error=str(e), response=raw_text[:200])
            raise ValueError(f"Failed to parse vision response as JSON: {e}")
        except ValueError as e:
            log.error("bedrock_vision_validation_error", error=str(e))
            raise

_service: BedrockVisionService | None = None


def get_bedrock_vision_service() -> BedrockVisionService:
    """Get or create singleton Bedrock vision service."""
    global _service
    if _service is None:
        _service = BedrockVisionService()
    return _service