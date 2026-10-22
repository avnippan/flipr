"""
Comps provider interface.

A provider turns a search query into normalized listings. It does not compute
prices; pricing.py does that, so every provider gets the same math. Each
provider must declare whether its prices are completed sales or active asking
prices. That value travels with the result so the UI and listing prompts can
label the data honestly.
"""

from typing import Literal, Protocol

from pydantic import BaseModel

CompDataType = Literal["sold", "active"]


class CompListing(BaseModel):
    """One listing from a provider, normalized across sources."""

    title: str
    price: float
    shipping: float | None = None
    currency: str = "USD"
    url: str | None = None


class CompProvider(Protocol):
    """Source of comparable listings."""

    # Short stable id, matches the COMPS_PROVIDER setting and is stored on results.
    name: str
    # "sold" only if every listing returned is a completed sale.
    data_type: CompDataType

    async def search(self, query: str) -> list[CompListing]:
        """Return listings for the query, or [] when nothing matches.

        Raise on transport or auth failures; do not swallow them as [].
        """
        ...
