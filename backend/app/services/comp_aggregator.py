"""
Cross-market comp aggregator.

Orchestrates pricing intelligence across all 4 sources:
  - eBay Browse API (sold comps) — pre-fetched by the pipeline,
    passed in as CompResult to avoid a redundant API call
  - Mercari (sold comps) — Playwright XHR scraper
  - Depop (active asking prices) — httpx REST scraper
  - Poshmark (active asking prices) — httpx HTML scraper

All 3 scrapers run concurrently via asyncio.gather with
return_exceptions=True so one failing platform never kills the others.
Each platform's results are converted to PlatformPricing stats
(min, median, max, sample_size). The aggregator then derives:
  - recommended_platform: platform with highest median sold price
    and sufficient sample size (≥5 comps)
  - pricing_confidence: based on sold comp coverage and sample size
"""

import asyncio
import statistics
from typing import Any

import structlog

from app.models.item import (
    AggregatedPricing,
    CompResult,
    PlatformPricing,
)
from app.services.scrapers.depop_scraper import scrape_depop_listings
from app.services.scrapers.mercari_scraper import scrape_mercari_sold_comps
from app.services.scrapers.poshmark_scraper import scrape_poshmark_listings

logger = structlog.get_logger(__name__)

# Minimum comps needed for a platform to be considered in recommendation
_MIN_SAMPLE_FOR_RECOMMENDATION = 5

# Thresholds for pricing_confidence scoring
_HIGH_CONFIDENCE_MIN_SOLD_PLATFORMS = 2
_HIGH_CONFIDENCE_MIN_SOLD_SAMPLES = 20
_MEDIUM_CONFIDENCE_MIN_SOLD_PLATFORMS = 1
_MEDIUM_CONFIDENCE_MIN_ASKING_SAMPLES = 20


async def aggregate_comps(
    search_query: str,
    ebay_result: CompResult,
) -> AggregatedPricing:
    """
    Aggregate cross-market pricing from eBay + 3 scrapers.

    Converts the pre-fetched eBay CompResult to PlatformPricing,
    runs Mercari/Depop/Poshmark scrapers concurrently, converts
    each result to PlatformPricing, then computes recommended_platform
    and pricing_confidence across all sources.

    Uses asyncio.gather(return_exceptions=True) so scraper failures
    are captured as Exception objects rather than propagating —
    a failed platform is logged and skipped, not fatal.

    Args:
        search_query: The item search string used for all scrapers.
        ebay_result: Pre-fetched CompResult from the existing eBay
                     pricing pipeline. Avoids a redundant API call.

    Returns:
        AggregatedPricing with per-platform stats and recommendation.
    """
    # Run all 3 scrapers concurrently
    mercari_result, depop_result, poshmark_result = await asyncio.gather(
        scrape_mercari_sold_comps(search_query),
        scrape_depop_listings(search_query),
        scrape_poshmark_listings(search_query),
        return_exceptions=True,
    )

    # Convert eBay CompResult → PlatformPricing
    ebay_pricing = _ebay_to_platform_pricing(ebay_result)

    # Convert scraper results → PlatformPricing (skip failures)
    mercari_pricing = _comps_to_platform_pricing(
        mercari_result, "mercari"
    )
    depop_pricing = _comps_to_platform_pricing(
        depop_result, "depop"
    )
    poshmark_pricing = _comps_to_platform_pricing(
        poshmark_result, "poshmark"
    )

    aggregated = AggregatedPricing(
        ebay=ebay_pricing,
        mercari=mercari_pricing,
        depop=depop_pricing,
        poshmark=poshmark_pricing,
    )

    aggregated.recommended_platform = _recommend_platform(aggregated)
    aggregated.pricing_confidence = _score_confidence(aggregated)

    return aggregated


def _ebay_to_platform_pricing(result: CompResult) -> PlatformPricing | None:
    """
    Convert an eBay CompResult to PlatformPricing.

    CompResult already has min/median/high prices computed by the
    existing pricing pipeline. We map these directly rather than
    recomputing from raw_comps.

    Returns None if sample_size is 0 — no usable eBay data.
    """
    if result.sample_size == 0:
        return None

    return PlatformPricing(
        min_price=result.low_price,
        median_price=result.median_price,
        max_price=result.high_price,
        sample_size=result.sample_size,
        price_type="sold",
    )


def _comps_to_platform_pricing(
    result: Any,
    platform: str,
) -> PlatformPricing | None:
    """
    Convert a list of ScrapedComp objects to PlatformPricing.

    Handles three cases:
    1. result is an Exception — scraper failed, log and return None
    2. result is an empty list — no comps found, return None
    3. result is a non-empty list — compute stats and return PlatformPricing

    Uses statistics.median() for the median price. min/max from
    Python builtins. price_type taken from the first comp since all
    comps from one scraper share the same price_type.
    """
    if isinstance(result, Exception):
        logger.warning(
            f"{platform} scraper failed",
            error=str(result),
        )
        return None

    if not result:
        logger.debug(f"{platform} scraper returned no results")
        return None

    prices = [comp.sold_price for comp in result]
    price_type = result[0].price_type

    return PlatformPricing(
        min_price=min(prices),
        median_price=statistics.median(prices),
        max_price=max(prices),
        sample_size=len(prices),
        price_type=price_type,
    )


def _recommend_platform(aggregated: AggregatedPricing) -> str | None:
    """
    Recommend the platform with the best pricing opportunity.

    Prioritizes sold comp platforms (eBay, Mercari) over asking price
    platforms (Depop, Poshmark) since sold prices are ground truth.
    Among eligible platforms, picks the one with the highest median
    price — higher median means more seller-favorable market.

    A platform is eligible if its sample_size >= _MIN_SAMPLE_FOR_RECOMMENDATION.
    If no platform meets the threshold, returns None.
    """
    # Sold comp platforms take priority
    sold_candidates = {
        "ebay": aggregated.ebay,
        "mercari": aggregated.mercari,
    }

    # Asking price platforms as fallback
    asking_candidates = {
        "depop": aggregated.depop,
        "poshmark": aggregated.poshmark,
    }

    for candidates in (sold_candidates, asking_candidates):
        eligible = {
            name: pricing
            for name, pricing in candidates.items()
            if pricing is not None
            and pricing.sample_size >= _MIN_SAMPLE_FOR_RECOMMENDATION
        }

        if eligible:
            return max(
                eligible,
                key=lambda name: eligible[name].median_price,
            )

    return None


def _score_confidence(aggregated: AggregatedPricing) -> str:
    """
    Score overall pricing confidence based on sold comp coverage.

    High:   2+ sold comp platforms each with sufficient samples
    Medium: 1+ sold comp platform with any samples, OR asking
            prices from 20+ comps total
    Low:    everything else — sparse data, all asking prices,
            or all scrapers failed

    Sold comps (eBay, Mercari) carry more weight than asking prices
    (Depop, Poshmark) because they reflect actual completed sales.
    """
    sold_platforms = [
        p for p in [aggregated.ebay, aggregated.mercari]
        if p is not None and p.sample_size > 0
    ]
    total_sold_samples = sum(p.sample_size for p in sold_platforms)

    asking_platforms = [
        p for p in [aggregated.depop, aggregated.poshmark]
        if p is not None and p.sample_size > 0
    ]
    total_asking_samples = sum(p.sample_size for p in asking_platforms)

    if (
        len(sold_platforms) >= _HIGH_CONFIDENCE_MIN_SOLD_PLATFORMS
        and total_sold_samples >= _HIGH_CONFIDENCE_MIN_SOLD_SAMPLES
    ):
        return "high"

    if (
        len(sold_platforms) >= _MEDIUM_CONFIDENCE_MIN_SOLD_PLATFORMS
        or total_asking_samples >= _MEDIUM_CONFIDENCE_MIN_ASKING_SAMPLES
    ):
        return "medium"

    return "low"
