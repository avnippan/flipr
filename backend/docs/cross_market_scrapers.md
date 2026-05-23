# Cross-Market Pricing Scrapers

## Overview

Async Playwright scrapers demonstrating cross-platform pricing
intelligence and comp aggregation across Poshmark, Mercari, and
Depop.

This module is a **research prototype** — it demonstrates the
async scraping pipeline and aggregation layer. Production application
uses the eBay Browse API as its primary pricing intelligence
source.

## Architecture

- Async Playwright scrapers running concurrently via
  `asyncio.gather`
- Cross-platform data normalization into a unified `CompResult`
  schema
- Failure-tolerant aggregation — pipeline continues if any
  platform is unavailable
- Confidence scoring: high / medium / low based on sample size
  and cross-platform agreement
- Structured logging via structlog throughout

## Poshmark Scraper

- Parses `__INITIAL_STATE__` JSON blob embedded in page HTML
- Extracts asking and sold prices from search results
- Handles trailing JavaScript after JSON object via
  `JSONDecoder().raw_decode()`
- Yield: ~30 comps per query

## Comp Aggregator

- Accepts `CompResult` objects from any combination of platforms
- Calculates min / median / max per platform
- Recommends best listing platform based on sample size and
  price alignment
- Designed to degrade gracefully under partial data

## Technical Notes

Platform anti-bot measures (Cloudflare challenges, API
authentication requirements) are common in production scraping
environments. This prototype demonstrates how to architect a
failure-tolerant aggregation layer that handles partial
availability — a pattern applicable to any multi-source data
pipeline.

## Production Architecture

Production FLIPR uses the **eBay Browse API** for pricing
intelligence — fully supported, ToS-compliant, and reliable.
The scraper architecture here informed the aggregation design
used in production.

## Legal Notice

Automated scraping may conflict with platform Terms of Service.
This code is for research and architectural demonstration only
and is not used in production.
