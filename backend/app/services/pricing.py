import statistics
import structlog

from app.models.item import CompResult, SoldComp
from app.services.comps import CompListing, CompProvider, get_provider

logger = structlog.get_logger(__name__)


def _build_fallback_query(search_query: str) -> str | None:
    """Drop the last word from the query for a broader retry search."""
    words = search_query.rsplit(" ", 1)
    return words[0] if len(words) > 1 else None


def _calculate_comp_result(
    search_query: str, listings: list[CompListing], provider: CompProvider
) -> CompResult:
    """Aggregate provider listings into a CompResult with pricing stats."""
    usd = [l for l in listings if l.currency == "USD"]
    prices = [l.price for l in usd]

    if not prices:
        raise ValueError(f"No USD-priced comps found for '{search_query}'")

    median = statistics.median(prices)

    raw_comps = [
        SoldComp(title=l.title, sold_price=l.price)
        for l in usd[:10]  # store top 10 for UI display
    ]

    return CompResult(
        search_query=search_query,
        data_type=provider.data_type,
        source=provider.name,
        sample_size=len(prices),
        low_price=round(min(prices), 2),
        median_price=round(median, 2),
        high_price=round(max(prices), 2),
        suggested_price=round(median * 0.95, 2),  # price to move, not just comp
        raw_comps=raw_comps,
    )


async def fetch_comps(
    search_query: str, provider: CompProvider | None = None
) -> CompResult:
    """
    Look up comparable listings for a search query and return pricing stats.
    The provider (COMPS_PROVIDER by default) decides where the data comes from
    and whether it is sold or active; the result carries that label.
    Falls back to a broader query (drops last word) if no results found.
    """
    provider = provider or get_provider()
    log = logger.bind(search_query=search_query, provider=provider.name)
    log.info("pricing_fetch_start", data_type=provider.data_type)

    query: str | None = search_query
    while query:
        listings = await provider.search(query)
        if any(l.currency == "USD" for l in listings):
            break
        fallback = _build_fallback_query(query)
        if fallback:
            log.warning("pricing_no_results_retrying_broader", fallback_query=fallback)
        query = fallback
    else:
        raise ValueError(f"No comps found for '{search_query}' after broadening query")

    result = _calculate_comp_result(query, listings, provider)

    log.info(
        "pricing_fetch_complete",
        data_type=result.data_type,
        sample_size=result.sample_size,
        median_price=result.median_price,
        suggested_price=result.suggested_price,
    )

    return result


# Deprecated name, kept so older callers keep working. It returns whatever the
# configured provider supplies, which may be active listings; check
# CompResult.data_type before calling the prices "sold".
fetch_sold_comps = fetch_comps
