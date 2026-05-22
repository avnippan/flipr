from app.models.item import AggregatedPricing, CompResult, ItemMetadata, ListingDraft
from pydantic import BaseModel


class AnalyzeResponse(BaseModel):
    metadata: ItemMetadata
    comps: CompResult
    listings: list[ListingDraft]
    aggregated_pricing: AggregatedPricing | None = None


class PostEbayResponse(BaseModel):
    success: bool
    listing_url: str | None = None
    listing_id: str | None = None
    error: str | None = None