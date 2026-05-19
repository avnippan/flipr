from app.models.item import ItemMetadata, CompResult, ListingDraft
from pydantic import BaseModel


class AnalyzeResponse(BaseModel):
    metadata: ItemMetadata
    comps: CompResult
    listings: list[ListingDraft]


class PostEbayResponse(BaseModel):
    success: bool
    listing_url: str | None = None
    listing_id: str | None = None
    error: str | None = None