from pydantic import BaseModel, Field
from typing import Literal


class ItemMetadata(BaseModel):
    """Structured output from GPT-4o vision analysis."""

    brand: str | None = None
    item_type: str                    # "denim jacket", "sneakers", "graphic tee"
    color: str | None = None
    size: str | None = None
    condition: Literal["poor", "fair", "good", "excellent"] | None = None
    material: str | None = None
    notable_details: list[str] = Field(default_factory=list)  # ["distressed knees", "vintage logo"]

    # The most important field — GPT-4o assembles this for eBay lookup
    # Good: "Levi's 501 jeans dark wash"
    # Bad: "vintage blue denim pants" (too generic, comps will be useless)
    search_query: str


class SoldComp(BaseModel):
    """A single eBay sold listing used as a price reference."""

    title: str
    sold_price: float
    currency: str = "USD"


class CompResult(BaseModel):
    """Aggregated pricing intelligence from eBay sold listings."""

    search_query: str
    sample_size: int
    low_price: float
    median_price: float
    high_price: float

    # Slightly under median — price to move, not just comp (median * 0.95)
    suggested_price: float

    # Raw comps stored for display in the UI ("We found 23 sales at $28–$58")
    raw_comps: list[SoldComp] = Field(default_factory=list)


class ListingDraft(BaseModel):
    """A platform-specific listing ready for review and posting."""

    platform: Literal["poshmark", "ebay"]
    title: str
    description: str
    suggested_price: float
    category_hint: str
    hashtags: list[str] = Field(default_factory=list)  # Poshmark uses these; eBay ignores them


class AnalysisResult(BaseModel):
    """The complete pipeline output for a single item."""

    item: ItemMetadata
    comps: CompResult
    listings: list[ListingDraft]