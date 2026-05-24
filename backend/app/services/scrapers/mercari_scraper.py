"""
Mercari sold listings scraper.

Mercari US (mercari.com) is a Next.js SPA — the search page fetches
listing data via XHR after the JS runtime initializes. Raw httpx
requests return a shell HTML page with no listing data. Playwright
is required to let the page hydrate, then we intercept the XHR
network response directly instead of parsing the DOM.

This is faster and more stable than DOM scraping: the JSON payload
is the source of truth, and field names change less often than
CSS selectors or HTML structure.

URL pattern:
    https://www.mercari.com/search/?keyword={query}&status=sold_out

Status values:
    sold_out  → sold listings (what we want for comps)
    on_sale   → active listings

The XHR response contains per-listing fields including:
    id, name, price, status, condition, created, updated
"""

import asyncio
import logging
from typing import Any

from playwright.async_api import async_playwright, Page, Response

from app.models.item import ScrapedComp

logger = logging.getLogger(__name__)

# How long to wait for the XHR response before giving up (ms)
_XHR_TIMEOUT_MS = 15_000

# Maximum number of sold comps to return
_DEFAULT_MAX_RESULTS = 25

# Mercari search URL with sold filter
_SEARCH_URL = "https://www.mercari.com/search/?keyword={query}&status=sold_out"

# The XHR endpoint Mercari's frontend calls to fetch search results.
# We intercept responses from this URL pattern to extract listing data
# without parsing the DOM.
_API_URL_PATTERN = "mercari.com/v1/api"


async def scrape_mercari_sold_comps(
    search_query: str,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> list[ScrapedComp]:
    """
    Scrape Mercari US for sold listings matching search_query.

    Launches a headless Chromium browser, navigates to the Mercari
    search page with status=sold_out, and intercepts the XHR response
    containing listing data. Returns up to max_results ScrapedComp
    objects.

    Exceptions are NOT caught here — they propagate to the aggregator
    which uses asyncio.gather(return_exceptions=True) to handle
    per-scraper failures without killing other platform scrapers.

    Args:
        search_query: Item search string, e.g. "Thrasher tee green M"
        max_results: Cap on returned comps. Mercari pages return ~30
                     items; we slice to max_results after filtering.

    Returns:
        List of ScrapedComp with platform="mercari"
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            results = await _fetch_sold_listings(page, search_query, max_results)
        finally:
            await browser.close()

        return results


async def _fetch_sold_listings(
    page: Page,
    search_query: str,
    max_results: int,
) -> list[ScrapedComp]:
    """
    Navigate to Mercari search and intercept the XHR response.

    Mercari fires an XHR to its internal search API shortly after
    page load. We register a response handler before navigating so
    we don't miss it, then wait for the response event to fire.

    The asyncio.Event lets us bridge Playwright's callback-based
    response handler into an awaitable pattern without polling.
    """
    # asyncio.Event used to signal when the XHR response has arrived.
    # The response handler sets it; we await it below with a timeout.
    xhr_received = asyncio.Event()
    raw_items: list[dict[str, Any]] = []

    async def handle_response(response: Response) -> None:
        """
        Intercept XHR responses and extract listing data.

        Filters to only the Mercari search API URL. On match,
        parses the JSON body and signals the waiting coroutine.
        """
        if _API_URL_PATTERN not in response.url:
            return

        try:
            body = await response.json()
            # Mercari's response shape: { "data": { "items": [...] } }
            # Field names may shift — guard every access
            items = body.get("data", {}).get("search", {}).get("itemsList", [])
            raw_items.extend(items)
        except Exception as exc:
            logger.debug("mercari XHR parse failed", error=str(exc))
        finally:
            xhr_received.set()

    page.on("response", handle_response)

    url = _SEARCH_URL.format(query=search_query.replace(" ", "+"))
    await page.goto(url, wait_until="domcontentloaded")

    # Wait for the XHR to fire (up to _XHR_TIMEOUT_MS)
    try:
        await asyncio.wait_for(
            xhr_received.wait(),
            timeout=_XHR_TIMEOUT_MS / 1000,
        )
    except asyncio.TimeoutError:
        logger.warning("mercari XHR timeout", query=search_query)
        return []

    return _parse_items(raw_items, max_results)


def _parse_items(
    raw_items: list[dict[str, Any]],
    max_results: int,
) -> list[ScrapedComp]:
    """
    Convert raw Mercari API items into ScrapedComp objects.

    Filters to sold_out status only (the URL filter should already
    do this, but we verify defensively). Skips items with missing
    or unparseable prices. Truncates to max_results.

    Mercari prices are integers in USD cents (e.g. 2500 = $25.00).
    """
    comps: list[ScrapedComp] = []

    for item in raw_items:
        # Defensive: confirm sold status even though URL filters for it
        if item.get("status") != "sold_out":
            continue

        price_raw = item.get("price")
        if price_raw is None:
            continue

        try:
            # Mercari returns price as integer cents
            price = float(price_raw) / 100
        except (TypeError, ValueError):
            continue

        title = item.get("name", "")
        item_id = item.get("id", "")
        url = f"https://www.mercari.com/item/{item_id}/" if item_id else None
        condition = _map_condition(item.get("item_condition", {}).get("name"))

        comps.append(
            ScrapedComp(
                platform="mercari",
                sold_price=price,
                title=title,
                price_type="sold",
                url=url,
                condition=condition,
            )
        )

        if len(comps) >= max_results:
            break

    return comps


def _map_condition(raw: str | None) -> str | None:
    """
    Normalize Mercari condition labels to FLIPR's condition vocabulary.

    Mercari uses: "New", "Like New", "Good", "Fair", "Poor"
    FLIPR uses:   "excellent", "good", "fair", "poor"

    Returns None for unrecognized or missing values rather than
    guessing — the aggregator handles None gracefully.
    """
    if not raw:
        return None

    mapping = {
        "new": "excellent",
        "like new": "excellent",
        "good": "good",
        "fair": "fair",
        "poor": "poor",
    }

    return mapping.get(raw.lower())
