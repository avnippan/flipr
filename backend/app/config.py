from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # OpenAI
    openai_api_key: str = ""
    openai_vision_model: str = "gpt-4o"

    # eBay 
    ebay_app_id: str
    ebay_cert_id: str
    ebay_user_token: str = ""
    ebay_fulfillment_policy_id: str = ""
    ebay_payment_policy_id: str = ""
    ebay_return_policy_id: str = ""
    ebay_merchant_location_key: str = "flipr_warehouse"

    # File upload limits
    max_upload_size_mb: int = 10
    allowed_image_types: list[str] = ["image/jpeg", "image/png", "image/webp"]

    # Stub auth — single API key until JWT is wired in
    api_key: str = "dev-key"

    # AWS
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_region: str = "us-east-1"
    s3_bucket_name: str = ""
    # Jobs table: env var is DYNAMODB_TABLE_NAME for legacy reasons; Lambda handlers read it directly
    dynamodb_table_name: str = "flipr-jobs"
    tokens_table_name: str        # TOKENS_TABLE_NAME
    rate_limits_table_name: str   # RATE_LIMITS_TABLE_NAME

    # Runtime context
    environment: str = Field(default="dev", env="ENVIRONMENT")

    # Bedrock
    bedrock_model_id: str
    use_bedrock: bool = True
    
    # Comps source: ebay_browse (active listings) or stub. See app/services/comps.
    comps_provider: str = "ebay_browse"

    # Bedrock Guardrails
    bedrock_guardrail_id: str = ""
    bedrock_guardrail_version: str = "DRAFT"


# Single shared instance — import this everywhere, never instantiate Settings() twice
settings = Settings()