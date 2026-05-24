import asyncio
import json
import structlog
import boto3
from botocore.exceptions import ClientError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings
from app.models.item import ItemMetadata, CompResult, ListingDraft
from app.services.listing import _build_prompt, _get_system_prompt, _validate_listing, score_listing

logger = structlog.get_logger(__name__)


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


class BedrockListingService:
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
    def _call_converse(self, system_prompt: str, user_prompt: str) -> str:
        """Call Bedrock Converse API with system and user prompts."""
        try:
            converse_kwargs = {
                "modelId": settings.bedrock_model_id,
                "system": [{"text": system_prompt}],
                "messages": [{"role": "user", "content": [{"text": user_prompt}]}],
                "inferenceConfig": {
                    "temperature": 0.7,
                    "maxTokens": 1200,
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
                logger.warning("bedrock_listing_guardrail_block", error=error_msg)
                raise ValueError("Unable to generate listing due to content policy.")
            
            logger.error(
                "bedrock_listing_client_error",
                code=error_code,
                error=error_msg,
            )
            raise

        return response["output"]["message"]["content"][0]["text"]

    async def draft_for_platform(
        self, item: ItemMetadata, comps: CompResult, platform: str
    ) -> ListingDraft:
        """Generate listing draft for platform using Bedrock."""
        log = logger.bind(platform=platform, item_type=item.item_type, brand=item.brand)
        log.info("bedrock_listing_start")

        try:
            raw_text = await asyncio.to_thread(
                self._call_converse,
                _get_system_prompt(platform),
                _build_prompt(item, comps, platform),
            )
            
            # Check for guardrail block message
            if "content policy restrictions" in raw_text.lower():
                log.warning("bedrock_listing_guardrail_blocked", response=raw_text)
                raise ValueError(raw_text)  # Return the guardrail message directly
            
            clean_json = _strip_json_markdown(raw_text)
            draft = ListingDraft(**json.loads(clean_json))
            draft.platform = platform
            draft = _validate_listing(draft, item, platform)

            scores = score_listing(draft, item, platform)
            log.info(
                "bedrock_listing_complete",
                title=draft.title,
                price=draft.suggested_price,
                quality_score=scores["overall"],
            )
            return draft
        
        except json.JSONDecodeError as e:
            log.error(
                "bedrock_listing_json_parse_error",
                error=str(e),
                response=raw_text[:200],
            )
            raise ValueError(f"Failed to parse listing response as JSON: {e}")
        except ValueError as e:
            log.error("bedrock_listing_validation_error", error=str(e))
            raise


_service: BedrockListingService | None = None


def get_bedrock_listing_service() -> BedrockListingService:
    """Get or create singleton Bedrock listing service."""
    global _service
    if _service is None:
        _service = BedrockListingService()
    return _service