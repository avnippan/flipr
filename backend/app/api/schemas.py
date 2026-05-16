# schemas.py — replace entire file with this
from app.models.item import ItemMetadata, CompResult, ListingDraft
from pydantic import BaseModel

class AnalyzeResponse(BaseModel):
    metadata: ItemMetadata
    comps: CompResult
    listings: list[ListingDraft]