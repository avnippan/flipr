"""
Marketplace fee schedules and net-profit math.

Net profit = sale price - platform fee - shipping paid by the seller - cost paid.
Fees and shipping are public rules, so this does not depend on comp data; the
profit is only as good as the price fed in.

Assumption: the price is what the buyer pays for the item with shipping handled
as in each platform's default (eBay and Mercari: free shipping, the seller pays
the label; Poshmark: the buyer pays Poshmark's prepaid label). Fees are charged
on that price. eBay also charges its fee on shipping and sales tax when the
buyer pays them, which this does not model.

The browser mirrors this table in index.html (FEE_SCHEDULE) because the page
computes profit live as the reseller types a cost. tests/unit/test_fees.py
fails if the two drift apart. Change both together.
"""

from typing import TypedDict


class Tier(TypedDict, total=False):
    max_price: float  # tier applies while price <= max_price; omitted = no upper bound
    pct: float  # percent of sale price
    flat: float  # fixed dollars per order


class PlatformFees(TypedDict):
    label: str
    tiers: list[Tier]
    default_shipping: float  # seller-paid shipping estimate, editable in the UI
    note: str


# Verified = read on the platform's own page on 2026-10-22. See docs/fees.md.
FEE_SCHEDULE: dict[str, PlatformFees] = {
    "ebay": {
        "label": "eBay",
        # Verified: 13.6% up to $7,500 plus $0.30 per order (<= $10) or $0.40
        # (> $10). The 13.6% rate is the "most categories" rate; the page
        # lookup did not show a Clothing row, so clothing is assumed to use
        # it. Sneakers, handbags and watches have their own rates, not modeled.
        "tiers": [
            {"max_price": 10.0, "pct": 13.6, "flat": 0.30},
            {"pct": 13.6, "flat": 0.40},
        ],
        # Assumed: not from a carrier rate card.
        "default_shipping": 6.0,
        "note": "13.6% + $0.30/$0.40 per order",
    },
    "poshmark": {
        "label": "Poshmark",
        # Not verified from an official page (poshmark.com blocked the fetch);
        # taken from third-party fee guides and the app's existing earnings math.
        "tiers": [
            {"max_price": 14.99, "pct": 0.0, "flat": 2.95},
            {"pct": 20.0, "flat": 0.0},
        ],
        # Buyer pays Poshmark's prepaid label.
        "default_shipping": 0.0,
        "note": "$2.95 under $15, 20% at $15 and over",
    },
    "mercari": {
        "label": "Mercari",
        # Not verified from an official page (mercari.com help center blocked
        # the fetch). Third-party guides: 10% selling fee + ~2.9% + $0.30
        # payment processing, combined here. Mercari changes this often.
        "tiers": [{"pct": 12.9, "flat": 0.30}],
        # Assumed: not from a carrier rate card.
        "default_shipping": 6.0,
        "note": "10% selling + ~2.9% + $0.30 processing",
    },
}


def platform_fee(platform: str, price: float) -> float:
    """Fee the platform takes on a sale at this price."""
    if price <= 0:
        return 0.0
    for tier in FEE_SCHEDULE[platform]["tiers"]:
        if price <= tier.get("max_price", float("inf")):
            return round(price * tier["pct"] / 100 + tier["flat"], 2)
    raise AssertionError("fee schedule has no open-ended tier")


def net_profit(
    platform: str,
    price: float,
    cost_paid: float = 0.0,
    shipping: float | None = None,
) -> dict:
    """Break a sale down into fee, shipping, cost and net profit.

    shipping=None uses the platform's default estimate.
    """
    if shipping is None:
        shipping = FEE_SCHEDULE[platform]["default_shipping"]
    fee = platform_fee(platform, price)
    net = round(price - fee - shipping - cost_paid, 2)
    return {
        "platform": platform,
        "price": round(price, 2),
        "fee": fee,
        "shipping": round(shipping, 2),
        "cost_paid": round(cost_paid, 2),
        "net": net,
        "margin_pct": round(net / price * 100, 1) if price > 0 else None,
    }


def best_platform(prices: dict[str, float], cost_paid: float = 0.0) -> str:
    """Platform with the highest net profit at the given per-platform prices."""
    return max(prices, key=lambda p: net_profit(p, prices[p], cost_paid)["net"])


def batch_totals(rows: list[dict]) -> dict:
    """Sum net_profit() results across a batch."""
    return {
        "items": len(rows),
        "revenue": round(sum(r["price"] for r in rows), 2),
        "fees": round(sum(r["fee"] for r in rows), 2),
        "shipping": round(sum(r["shipping"] for r in rows), 2),
        "cost_paid": round(sum(r["cost_paid"] for r in rows), 2),
        "net": round(sum(r["net"] for r in rows), 2),
    }
