"""
Depop active listings scraper.

Depop exposes a clean REST API at www.depop.com/api/v3/search/products/
that returns structured JSON without requiring JavaScript execution.
Unlike Mercari, no Playwright or XHR interception is needed — a simple
httpx GET request returns listing data directly.

Depop does not expose sold listings via public search. This scraper
returns active asking prices, labeled price_type="asking". The comp
aggregator weights these lower than sold comps from eBay and Mercari.

URL pattern:
    https://www.depop.com/api/v3/search/products/
    ?what={query}&country=us&currency=USD&items_per_page=24

Price path:
    Use final_price_key to determine which price object is active
    (original_price or discounted_price), then extract:
    pricing.{final_price_key}.price_breakdown.price.amount

Pagination:
    Response includes meta.cursor for next page and meta.has_more flag.
    We fetch one page only — 24 results is sufficient for comp pricing.
"""

from typing import Any

import httpx
import structlog

from app.models.item import ScrapedComp

logger = structlog.get_logger(__name__)

_DEFAULT_MAX_RESULTS = 24
_TIMEOUT_SECONDS = 10.0

_SEARCH_URL = "https://www.depop.com/api/v3/search/products/"

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    # Depop expects a browser-like user agent — bare httpx gets blocked
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


async def scrape_depop_listings(
    search_query: str,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> list[ScrapedComp]:
    """
    Fetch active Depop listings matching search_query via REST API.

    Makes a single GET request to Depop's product search endpoint.
    No browser automation required — Depop's API returns structured
    JSON directly. Returns asking prices labeled price_type="asking"
    since Depop does not expose sold listing data via public search.

    Exceptions are NOT caught here — they propagate to the aggregator
    which uses asyncio.gather(return_exceptions=True) to handle
    per-scraper failures without killing other platform scrapers.

    Args:
        search_query: Item search string, e.g. "Thrasher tee green M"
        max_results: Cap on returned listings. Depop returns 24 per
                     page by default; we slice to max_results.

    Returns:
        List of ScrapedComp with platform="depop", price_type="asking"
    """
    params = {
        "what": search_query,
        "country": "us",
        "currency": "USD",
        "items_per_page": max_results,
        "from": "in_country_search",
    }

    async with httpx.AsyncClient(
        headers=_DEFAULT_HEADERS,
        timeout=_TIMEOUT_SECONDS,
        follow_redirects=True,
        verify=False,
    ) as client:
        response = await client.get(_SEARCH_URL, params=params)
        response.raise_for_status()
        data = response.json()

    products = data.get("products", [])
    return _parse_products(products, max_results)


def _parse_products(
    products: list[dict[str, Any]],
    max_results: int,
) -> list[ScrapedComp]:
    """
    Convert raw Depop API product dicts into ScrapedComp objects.

    Price extraction uses final_price_key to select between
    original_price and discounted_price — Depop sets this field
    to indicate which price the buyer actually pays. Falls back
    to original_price if the key is missing or invalid.

    Skips items with missing or unparseable prices. Truncates to
    max_results.
    """
    comps: list[ScrapedComp] = []

    for product in products:
        price = _extract_price(product)
        if price is None:
            continue

        slug = product.get("slug", "")
        url = f"https://www.depop.com/products/{slug}/" if slug else None
        title = _extract_title(slug)

        comps.append(
            ScrapedComp(
                platform="depop",
                sold_price=price,
                title=title,
                price_type="asking",
                url=url,
            )
        )

        if len(comps) >= max_results:
            break

    return comps


def _extract_price(product: dict[str, Any]) -> float | None:
    """
    Extract the active price from a Depop product dict.

    Depop uses final_price_key to indicate whether original_price
    or discounted_price is the buyer-facing price. We follow that
    key rather than hardcoding one price object, so discounted
    items are priced correctly.

    Returns None if price is missing or cannot be parsed as float.
    """
    try:
        pricing = product.get("pricing", {})
        price_key = pricing.get("final_price_key", "original_price")

        # Fall back to original_price if key is unexpected
        if price_key not in ("original_price", "discounted_price"):
            price_key = "original_price"

        amount_str = (
            pricing
            .get(price_key, {})
            .get("price_breakdown", {})
            .get("price", {})
            .get("amount")
        )

        if amount_str is None:
            return None

        return float(amount_str)

    except (TypeError, ValueError, KeyError):
        return None


def _extract_title(slug: str) -> str:
    """
    Derive a human-readable title from the Depop slug.

    Depop search results don't include a title field at the
    product list level — only the slug is available. The slug
    encodes the title as hyphen-separated words followed by a
    hex suffix (e.g. 'thrasher-black-tee-5f71'). We strip the
    suffix and capitalize for display.

    Example:
        'thrasher-black-tee-5f71' → 'Thrasher Black Tee'
    """
    if not slug:
        return ""

    # Remove trailing hex identifier (4-char alphanumeric suffix)
    parts = slug.split("-")
    if len(parts) > 1 and len(parts[-1]) == 4:
        parts = parts[:-1]

    return " ".join(part.capitalize() for part in parts)
