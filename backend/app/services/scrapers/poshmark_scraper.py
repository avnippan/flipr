"""
Poshmark active listings scraper.

Poshmark is server-rendered — listing data is embedded in the HTML
page as a JSON blob assigned to window.__INITIAL_STATE__. No XHR
fires for listing data, so Playwright is not needed. We fetch the
HTML with httpx, extract the JSON blob with str.find(), and parse listings
from the known path: __INITIAL_STATE__.$_search.gridData.data

This is faster and more reliable than DOM scraping since the JSON
structure changes less often than HTML markup.

Poshmark does not expose sold listings via public search. This scraper
returns active asking prices labeled price_type="asking". The comp
aggregator weights these lower than eBay and Mercari sold comps.

URL pattern:
    https://poshmark.com/search
    ?query={query}&type=listings&src=dir
"""

import json
from typing import Any

import httpx
import structlog

from app.models.item import ScrapedComp

logger = structlog.get_logger(__name__)

_DEFAULT_MAX_RESULTS = 30
_TIMEOUT_SECONDS = 15.0

_SEARCH_URL = "https://poshmark.com/search"

_DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # Must look like a real browser or Poshmark returns a login wall
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


async def scrape_poshmark_listings(
    search_query: str,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> list[ScrapedComp]:
    """
    Fetch active Poshmark listings by extracting embedded JSON from HTML.

    Poshmark embeds all listing data in window.__INITIAL_STATE__ on
    the search results page. We fetch the HTML with httpx, extract
    the JSON blob with a regex, and navigate to the listings array
    at $_search.gridData.data.

    Returns asking prices labeled price_type="asking" since Poshmark
    does not expose sold listing data via public search.

    Exceptions are NOT caught here — they propagate to the aggregator
    which uses asyncio.gather(return_exceptions=True) to handle
    per-scraper failures without killing other platform scrapers.

    Args:
        search_query: Item search string, e.g. "Thrasher tee green M"
        max_results: Cap on returned listings.

    Returns:
        List of ScrapedComp with platform="poshmark", price_type="asking"
    """
    params = {
        "query": search_query,
        "type": "listings",
        "src": "dir",
    }

    async with httpx.AsyncClient(
        headers=_DEFAULT_HEADERS,
        timeout=_TIMEOUT_SECONDS,
        follow_redirects=True,
        verify=False,
    ) as client:
        response = await client.get(_SEARCH_URL, params=params)
        response.raise_for_status()
        html = response.text

    posts = _extract_posts(html)
    return _parse_posts(posts, max_results)


def _extract_posts(html: str) -> list[dict[str, Any]]:
    """
    Extract listing dicts from the __INITIAL_STATE__ JSON blob.

    Navigates the known path: $_search.gridData.data
    Returns empty list if the regex fails, JSON is malformed,
    or the expected path doesn't exist — callers handle empty
    results as low confidence, not a crash.
    """
    idx = html.find("window.__INITIAL_STATE__=")
    if idx == -1:
        logger.warning("poshmark: __INITIAL_STATE__ not found in HTML")
        return []

    start = idx + len("window.__INITIAL_STATE__=")
    end = html.find("</script>", start)
    if end == -1:
        logger.warning("poshmark: could not find end of __INITIAL_STATE__")
        return []

    raw = html[start:end]

    try:
        # raw_decode stops after the first valid JSON value, ignoring any
        # trailing JS (semicolons, more statements) before </script>
        state, _ = json.JSONDecoder().raw_decode(raw.lstrip())
    except json.JSONDecodeError as exc:
        logger.warning("poshmark: failed to parse __INITIAL_STATE__", error=str(exc))
        return []

    posts = (
        state
        .get("$_search", {})
        .get("gridData", {})
        .get("data", [])
    )

    if not isinstance(posts, list):
        logger.warning("poshmark: unexpected data type for posts", type=type(posts))
        return []

    return posts


def _parse_posts(
    posts: list[dict[str, Any]],
    max_results: int,
) -> list[ScrapedComp]:
    """
    Convert raw Poshmark post dicts into ScrapedComp objects.

    Uses price_amount.val as primary price (string float, e.g. "9.0"),
    falls back to price field (integer). Skips posts with missing or
    unparseable prices. Truncates to max_results.
    """
    comps: list[ScrapedComp] = []

    for post in posts:
        price = _extract_price(post)
        if price is None:
            continue

        title = post.get("title", "")
        post_id = post.get("id", "")
        url = (
            f"https://poshmark.com/listing/{post_id}"
            if post_id else None
        )

        comps.append(
            ScrapedComp(
                platform="poshmark",
                sold_price=price,
                title=title,
                price_type="asking",
                url=url,
            )
        )

        if len(comps) >= max_results:
            break

    return comps


def _extract_price(post: dict[str, Any]) -> float | None:
    """
    Extract price from a Poshmark post dict.

    Primary: price_amount.val (string float) — preserves cent precision.
    Fallback: price (integer) — always present but loses cent precision.

    Returns None if both are missing or unparseable.
    """
    try:
        val = post.get("price_amount", {}).get("val")
        if val is not None:
            return float(val)
    except (TypeError, ValueError):
        pass

    try:
        price_int = post.get("price")
        if price_int is not None:
            return float(price_int)
    except (TypeError, ValueError):
        pass

    return None
