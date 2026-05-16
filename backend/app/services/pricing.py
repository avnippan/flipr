import statistics
import structlog

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx

from app.config import settings
from app.models.item import CompResult, SoldComp

logger = structlog.get_logger(__name__)


def _build_fallback_query(search_query: str) -> str | None:
    """Drop the last word from the query for a broader retry search."""
    words = search_query.rsplit(" ", 1)
    return words[0] if len(words) > 1 else None


def _calculate_comp_result(search_query: str, items: list[dict]) -> CompResult:
    """Aggregate raw eBay items into a CompResult with pricing stats."""
    usd_items = [i for i in items if i.get("price", {}).get("currency") == "USD"]

    prices = [float(i["price"]["value"]) for i in usd_items if "price" in i]

    if not prices:
        raise ValueError(f"No USD-priced sold comps found for '{search_query}'")

    median = statistics.median(prices)

    raw_comps = [
        SoldComp(
            title=i.get("title", ""),
            sold_price=float(i["price"]["value"]),
        )
        for i in usd_items[:10]  # store top 10 for UI display
    ]

    return CompResult(
        search_query=search_query,
        sample_size=len(prices),
        low_price=round(min(prices), 2),
        median_price=round(median, 2),
        high_price=round(max(prices), 2),
        suggested_price=round(median * 0.95, 2),  # price to move, not just comp
        raw_comps=raw_comps,
    )


async def _get_ebay_token(client: httpx.AsyncClient) -> str:
    """Fetch eBay OAuth2 client credentials token. Valid for 2 hours."""
    import base64

    credentials = base64.b64encode(
        f"{settings.ebay_app_id}:{settings.ebay_cert_id}".encode()
    ).decode()

    resp = await client.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
)
async def fetch_sold_comps(search_query: str) -> CompResult:
    """
    Look up eBay sold listings for a search query and return pricing stats.
    Falls back to a broader query (drops last word) if no results found.
    """
    log = logger.bind(search_query=search_query)
    log.info("pricing_fetch_start")

    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        token = await _get_ebay_token(client)

        resp = await client.get(
            "https://api.ebay.com/buy/browse/v1/item_summary/search",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "q": search_query,
                "filter": "soldItems:true",      # completed sales only
                "limit": 50,
                "sort": "endTimeSoonest",        # most recent sales first
            },
        )
        resp.raise_for_status()
        items = resp.json().get("itemSummaries", [])

    if not items:
        fallback = _build_fallback_query(search_query)
        if fallback:
            log.warning("pricing_no_results_retrying_broader", fallback_query=fallback)
            return await fetch_sold_comps(fallback)
        raise ValueError(f"No sold comps found for '{search_query}' after broadening query")

    result = _calculate_comp_result(search_query, items)

    log.info(
        "pricing_fetch_complete",
        sample_size=result.sample_size,
        median_price=result.median_price,
        suggested_price=result.suggested_price,
    )

    return result


# --- Stub for development without eBay credentials ---

async def fetch_sold_comps_stub(search_query: str) -> CompResult:
    """
    Returns hardcoded comp data for local development.
    Replace fetch_sold_comps with this in tests or when eBay creds aren't available.
    """
    logger.info("pricing_stub_used", search_query=search_query)
    return CompResult(
        search_query=search_query,
        sample_size=12,
        low_price=18.00,
        median_price=34.00,
        high_price=58.00,
        suggested_price=32.30,
        raw_comps=[
            SoldComp(title=f"{search_query} example listing {i}", sold_price=20.0 + i * 3)
            for i in range(5)
        ],
    )