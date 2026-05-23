# Cross-Market Pricing Scrapers

## Status: Research Prototype

**Poshmark:** ✅ Working (~30 comps per search)  
**Mercari:** ❌ Blocked by Cloudflare bot protection  
**Depop:** ❌ Blocked by API authentication  
**Production use:** Not suitable for B2B product

## Overview

Async Playwright scrapers demonstrating cross-platform pricing comp aggregation. Built during Sprint 5 to explore multi-marketplace data integration.

**Key finding:** Marketplace scrapers are fragile (platforms actively block them) and violate Terms of Service. Production FLIPR uses eBay Browse API instead for compliant, reliable pricing intelligence.

## What Works

### Poshmark Scraper
- Extracts asking/sold prices from search results
- Parses `__INITIAL_STATE__` JSON blob in page HTML
- Yield: ~30 comps per query
- Example: "Nike Air Max 90 used" → median $62

### Comp Aggregator
- Accepts CompResult objects from multiple platforms
- Calculates min/median/max pricing per platform
- Recommends best platform based on sample size
- Confidence scoring: high/medium/low
- Handles platform failures gracefully

## What Doesn't Work

### Mercari
- Cloudflare bot protection blocks headless browsers
- Requires stealth tooling or residential proxies (not viable for SaaS)

### Depop
- `/api/v3/search/products/` now requires auth tokens
- Returns 403 Forbidden on unauthenticated requests

## Technical Highlights

- Async scraping with Playwright
- Cross-platform data normalization
- Failure-tolerant aggregation (works even if only 1 platform succeeds)
- Structured logging with kwargs support

## Production Architecture

FLIPR's B2B product uses **eBay Browse API** (compliant, supported, reliable) as the primary pricing intelligence source, with optional seller-provided CSV imports from other platforms.

Scrapers remain in the codebase as a technical demonstration of async data aggregation capability but are not used in production.

## Usage (Testing Only)

```python
from app.services.scrapers.poshmark_scraper import scrape_poshmark_listings
from app.services.comp_aggregator import aggregate_comps

# Poshmark scraper works
poshmark_comps = await scrape_poshmark_listings("Nike Air Max 90")
# Returns ~30 comps

# Mercari/Depop are blocked
mercari_comps = await scrape_mercari_sold_comps("Nike Air Max 90")  # Returns []
depop_comps = await scrape_depop_listings("Nike Air Max 90")  # Raises 403
```

## Legal Notice

Scraping violates Poshmark, Mercari, and Depop Terms of Service. This code is for research and technical demonstration only. Production B2B customers require ToS-compliant data sources.
