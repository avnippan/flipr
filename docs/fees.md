# Marketplace fees used for net profit

Net profit = price - platform fee - seller-paid shipping - cost paid.
Source of truth: `backend/app/services/fees.py`. `index.html` mirrors the table
(`FEE_SCHEDULE`); `tests/unit/test_fees.py` fails if they differ.

Last checked 2026-10-22.

| Platform | Rule used | Status |
|---|---|---|
| eBay | 13.6% of sale (up to $7,500) + $0.30 per order if $10.00 or less, $0.40 if over | Per-order fee and 13.6% general rate **verified** on ebay.com selling fees page. That the clothing category uses the general rate is **assumed**: the page lookup did not show a Clothing row. |
| eBay exceptions | Sneakers 13.6% under $150, 8% at $150+ with no per-order fee; handbags 15% up to $2,000; watches tiered | **Verified**, **not modeled**. Clothing is the target category. |
| Poshmark | $2.95 flat under $15, 20% at $15+ | **Not verified.** poshmark.com blocked the fetch. Matches third-party guides and the earnings math the app already had. |
| Mercari | 12.9% + $0.30 (10% selling + ~2.9% payment processing + $0.30) | **Not verified.** mercari.com help center blocked the fetch. From a third-party guide dated Sept 2026; Mercari changes fees often. Check before relying on it. |

## Shipping (all assumed, editable per item in the UI)
- eBay and Mercari: $6.00 seller-paid label (free-shipping listing).
- Poshmark: $0.00, the buyer pays Poshmark's prepaid label.

## Not modeled
eBay fees on shipping and sales tax when the buyer pays them, promoted
listings, Mercari/Poshmark promotions, returns, sales tax remittance.

## Prices
eBay price is the comp-based suggestion. Poshmark is eBay +10% (existing
heuristic). Mercari equals the eBay price (no heuristic). Profit is only as good
as these prices; the panel shows the comp range, count and source beside it.
